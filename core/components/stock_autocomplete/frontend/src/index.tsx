import React from "react";
import ReactDOM from "react-dom/client";
import { withStreamlitConnection } from "streamlit-component-lib";
import StockAutocomplete from "./StockAutocomplete";
import "./styles.css";

const Connected = withStreamlitConnection(StockAutocomplete);

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Connected />
  </React.StrictMode>
);
