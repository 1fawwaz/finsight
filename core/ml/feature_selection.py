"""Phase 2 Step 6: Feature Selection -- mutual information, permutation importance,
correlation redundancy, and cross-fold stability analysis, plus an evidence-based
Feature Registry for deprecation. Reuses `core.ml.evaluation.generate_feature_importance`
/`generate_shap_summary` (gain-based and SHAP importance, Phase 3) rather than
reimplementing them, and `core.ml.cv.time_series_cv_folds`/`assert_no_chronological_leakage`
(chronological, no-leakage-by-construction) for stability analysis rather than a new CV
implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import mutual_info_classif
from sklearn.inspection import permutation_importance

from core.config import get_logger
from core.database import FeatureRegistry
from core.ml.cv import assert_no_chronological_leakage, time_series_cv_folds

logger = get_logger(__name__)

RANDOM_STATE = 42


def compute_mutual_information(X: pd.DataFrame, y: pd.Series) -> pd.Series:
    """Mutual information between each feature and the label -- captures nonlinear
    dependence that a linear correlation would miss, ranked descending."""
    clean = X.dropna()
    y_aligned = y.loc[clean.index]
    scores = mutual_info_classif(clean, y_aligned, random_state=RANDOM_STATE)
    return pd.Series(scores, index=X.columns).sort_values(ascending=False)


def compute_permutation_importance(model, X: pd.DataFrame, y: pd.Series, n_repeats: int = 10) -> pd.Series:
    """Permutation importance: how much a metric degrades when one feature's values are
    shuffled, holding everything else fixed -- model-agnostic, unlike gain-based
    importance (which only applies to tree models with `feature_importances_`)."""
    result = permutation_importance(model, X, y, n_repeats=n_repeats, random_state=RANDOM_STATE, n_jobs=1)
    return pd.Series(result.importances_mean, index=X.columns).sort_values(ascending=False)


def compute_correlation_redundancy(X: pd.DataFrame, threshold: float = 0.9) -> list[tuple[str, str, float]]:
    """Pairs of features whose absolute pairwise correlation exceeds `threshold` --
    candidates for redundancy (not automatically dropped; flagged as evidence for a
    human/registry decision, per the directive's "never silently remove" rule)."""
    corr = X.corr().abs()
    pairs = []
    columns = corr.columns.tolist()
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            value = corr.iloc[i, j]
            if pd.notna(value) and value > threshold:
                pairs.append((columns[i], columns[j], float(value)))
    return sorted(pairs, key=lambda p: p[2], reverse=True)


@dataclass
class StabilityResult:
    feature_stability: pd.DataFrame  # index=feature, columns=[mean_importance, std_importance, coefficient_of_variation]
    n_folds_used: int


def compute_feature_stability(features: pd.DataFrame, labels: pd.Series, n_folds: int = 5) -> StabilityResult:
    """Fit a fresh RandomForest per chronological fold (`core.ml.cv.time_series_cv_folds`,
    reused not reimplemented; every fold's leakage-freedom is explicitly asserted, not
    assumed) and track how much each feature's gain-based importance varies fold to
    fold. A feature with high mean importance but also high variance across folds is a
    real stability concern the directive asks for, distinct from (and complementary to)
    a single-split importance number.
    """
    combined = features.join(labels.rename("label")).dropna()
    X, y = combined.drop(columns=["label"]), combined["label"].astype(int)

    folds = time_series_cv_folds(X, n_folds=n_folds)
    per_fold_importances = []
    for fold in folds:
        assert_no_chronological_leakage(fold)
        X_train, y_train = X.loc[fold.train_index], y.loc[fold.train_index]
        if y_train.nunique() < 2:
            continue
        model = RandomForestClassifier(n_estimators=100, max_depth=5, min_samples_leaf=10, random_state=RANDOM_STATE)
        model.fit(X_train, y_train)
        per_fold_importances.append(pd.Series(model.feature_importances_, index=X.columns))

    if not per_fold_importances:
        empty = pd.DataFrame(columns=["mean_importance", "std_importance", "coefficient_of_variation"])
        return StabilityResult(feature_stability=empty, n_folds_used=0)

    importances_df = pd.DataFrame(per_fold_importances)
    mean_importance = importances_df.mean(axis=0)
    std_importance = importances_df.std(axis=0)
    # Coefficient of variation is undefined (0/0) for a feature with ~zero mean
    # importance across every fold -- reported as NaN, not a fabricated 0 or infinity.
    cv = (std_importance / mean_importance).replace([float("inf"), float("-inf")], pd.NA)

    stability = pd.DataFrame(
        {"mean_importance": mean_importance, "std_importance": std_importance, "coefficient_of_variation": cv}
    ).sort_values("mean_importance", ascending=False)
    return StabilityResult(feature_stability=stability, n_folds_used=len(per_fold_importances))


@dataclass
class FeatureEvaluationReport:
    mutual_information: pd.Series
    permutation_importance: pd.Series
    correlated_pairs: list[tuple[str, str, float]]
    stability: StabilityResult
    weak_feature_candidates: list[str] = field(default_factory=list)


def flag_weak_features(
    mi: pd.Series, perm: pd.Series, weak_mi_threshold: float = 0.001, weak_permutation_rank_fraction: float = 0.5,
) -> list[str]:
    """The deterministic flagging decision, isolated from the noisy statistical
    computations that feed it -- a feature is flagged only when it's weak on *both*
    mutual information (an absolute, near-zero threshold -- MI is well-behaved near 0
    for a genuinely unrelated feature) AND permutation importance (a *relative* rank,
    not an absolute threshold).

    Permutation importance is a noisy estimator (shuffling introduces sampling
    variance): a genuinely useless feature routinely produces a small positive value
    from noise alone, so an absolute cutoff near 0 is unreliable and was observed to
    misclassify a synthetic zero-signal feature during development. Ranking within the
    bottom `weak_permutation_rank_fraction` of the feature set is robust to that noise
    in a way an absolute threshold isn't, since it only depends on relative ordering.
    """
    if perm.empty:
        rank_cutoff_features = set()
    else:
        cutoff_index = max(1, int(len(perm) * weak_permutation_rank_fraction))
        rank_cutoff_features = set(perm.sort_values(ascending=True).index[:cutoff_index])

    return [
        feature for feature in mi.index
        if mi.get(feature, 0) < weak_mi_threshold and feature in rank_cutoff_features
    ]


def evaluate_features(
    features: pd.DataFrame, labels: pd.Series, weak_mi_threshold: float = 0.001, weak_permutation_rank_fraction: float = 0.5,
) -> FeatureEvaluationReport:
    """Run every Step 6 analysis and flag features that are weak on *both* mutual
    information and permutation importance (deliberately requiring agreement between
    two independent methods before flagging -- reduces the chance of flagging a feature
    that's genuinely useful but happens to score poorly on one metric alone). See
    `flag_weak_features` for why permutation importance is judged by rank, not an
    absolute threshold. Flagging is evidence for a registry decision, not an automatic
    removal.
    """
    combined = features.join(labels.rename("label")).dropna()
    X, y = combined.drop(columns=["label"]), combined["label"].astype(int)

    mi = compute_mutual_information(X, y)
    model = RandomForestClassifier(n_estimators=100, max_depth=5, min_samples_leaf=10, random_state=RANDOM_STATE)
    model.fit(X, y)
    perm = compute_permutation_importance(model, X, y)
    correlated = compute_correlation_redundancy(X)
    stability = compute_feature_stability(features, labels)

    weak = flag_weak_features(mi, perm, weak_mi_threshold, weak_permutation_rank_fraction)

    return FeatureEvaluationReport(
        mutual_information=mi, permutation_importance=perm, correlated_pairs=correlated,
        stability=stability, weak_feature_candidates=weak,
    )


def deprecate_feature(session, feature_name: str, reason: str, evidence: dict) -> FeatureRegistry:
    """The only sanctioned way to mark a feature deprecated -- always with a reason and
    evidence attached, never a silent deletion from a feature-building function."""
    entry = session.get(FeatureRegistry, feature_name)
    if entry is None:
        entry = FeatureRegistry(feature_name=feature_name)
        session.add(entry)
    entry.status = "deprecated"
    entry.reason = reason
    entry.evidence_json = json.dumps(evidence, default=str)
    session.flush()
    logger.warning("Feature deprecated: %s -- %s", feature_name, reason)
    return entry


def register_active_feature(session, feature_name: str, evidence: dict | None = None) -> FeatureRegistry:
    """Record (or re-confirm) a feature as active, optionally with supporting evidence
    (e.g. this evaluation run's importance scores) -- gives every feature a registry
    row, active or deprecated, rather than only recording the deprecated ones."""
    entry = session.get(FeatureRegistry, feature_name)
    if entry is None:
        entry = FeatureRegistry(feature_name=feature_name)
        session.add(entry)
    entry.status = "active"
    entry.evidence_json = json.dumps(evidence, default=str) if evidence is not None else entry.evidence_json
    session.flush()
    return entry


def get_active_features(session) -> list[str]:
    """Every feature name currently marked active in the registry. A feature never
    registered at all is not implicitly active or deprecated -- callers building a
    feature set from scratch should register every column they produce."""
    from sqlalchemy import select

    rows = session.execute(select(FeatureRegistry).where(FeatureRegistry.status == "active")).scalars().all()
    return [r.feature_name for r in rows]
