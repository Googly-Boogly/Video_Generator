import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import App from "./App";
import Home from "./pages/Home";
import NewProject from "./pages/NewProject";
import StoryboardReview from "./pages/StoryboardReview";
import Keyframes from "./pages/Keyframes";
import Clips from "./pages/Clips";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />}>
          <Route index element={<Home />} />
          <Route path="new" element={<NewProject />} />
          <Route path="projects/:id" element={<StoryboardReview />} />
          <Route path="projects/:id/keyframes" element={<Keyframes />} />
          <Route path="projects/:id/clips" element={<Clips />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
