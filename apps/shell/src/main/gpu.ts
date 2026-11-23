/**
 * Какой картой рисовать.
 *
 * Замерено на этой машине: встроенная Intel даёт 163 кадра в секунду на
 * 1.3 млн треугольников (6.13 мс на кадр), RTX 3060 через ANGLE/Vulkan —
 * 888–949 (1.09 мс). Обе половины включения обязательны: без переменных PRIME
 * контекст не поднимается, без флагов ANGLE идёт по GL-пути и падает на
 * несовпадении визуалов X11 («Invalid visual ID requested»), потому что окно
 * создано с визуалом Intel.
 *
 * По умолчанию — встроенная: на 163 кадрах вьюпорт не узкое место, а
 * дискретная держит ноутбук горячим. Переключается переменной DATUM_GPU.
 */
import type { App } from "electron";

export type GpuMode = "auto" | "discrete" | "software";

export function chooseGpu(app: App): GpuMode {
  const mode = (process.env.DATUM_GPU ?? "auto") as GpuMode;

  if (mode === "discrete") {
    process.env.__NV_PRIME_RENDER_OFFLOAD = "1";
    process.env.__GLX_VENDOR_LIBRARY_NAME = "nvidia";
    app.commandLine.appendSwitch("use-angle", "vulkan");
    app.commandLine.appendSwitch("enable-features", "Vulkan");
  } else if (mode === "software") {
    app.commandLine.appendSwitch("disable-gpu");
  }
  return mode;
}
