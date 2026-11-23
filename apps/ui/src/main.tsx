import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app";
import { Fault } from "./fault";
import { useUi } from "./store/ui";
import { loadLayout } from "./transport/endpoint";
// Шрифты — в бандле, не с сети: окно грузится с петли, а CSP пускает шрифты
// только со своего происхождения. Грузятся лишь нужные наборы знаков
// (unicode-range): латиница и кириллица.
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("нет узла #root");
// Размеры панелей с прошлого запуска — ДО первой отрисовки: иначе окно сперва
// показалось бы с умолчаниями и дёрнулось.
useUi.getState().applyLayout(await loadLayout());
createRoot(root).render(
  <StrictMode>
    <Fault area="Окно">
      <App />
    </Fault>
  </StrictMode>,
);
