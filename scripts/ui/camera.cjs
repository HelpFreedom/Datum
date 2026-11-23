/**
 * Сценарий: камера без упоров и «Вернуть камеру».
 *
 *   1. Колесом к центру вида — ПОСТОЯННЫМ шагом: путь камеры за десять
 *      щелчков одинаков и далеко, и вблизи, и после пролёта сквозь центр
 *      вращения (у OrbitControls шаг — доля расстояния, и вблизи колесо почти
 *      стояло). И камера обязана пролететь дальше центра, а не упереться.
 *   2. «Домой» и колесом назад, за 6 м — сцена не обрезается дальней
 *      плоскостью: в центре кадра остаётся деталь (прежде за 5 м исчезало всё).
 *      Отдаление ограничено только 500 радиусами сцены — там она уже точка.
 *   3. «Вернуть камеру» (клавиша H) — камера там же, где была при открытии.
 *
 * Положение камеры берётся из отчёта окна (`.datum/window.json`, его же читает
 * агент): своего канала в сцену у сценария нет и не нужно.
 *
 *   DATUM_OUT — снимок после отдаления (необязательна)
 */
const fs = require('node:fs');
const path = require('node:path');

const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win }) {
  const project = process.argv.slice(1).map((a) => path.resolve(a))
    .find((a) => fs.existsSync(path.join(a, 'datum.toml')));
  if (!project) throw new Error('сценарию нужен проект в аргументах');
  const report = path.join(project, '.datum', 'window.json');
  const wc = win.webContents;

  /** Камера из отчёта окна — после затишья, когда окно его отправило. */
  async function camera() {
    await sleep(1200);
    return JSON.parse(fs.readFileSync(report, 'utf-8')).camera;
  }
  const sub = (a, b) => a.map((v, i) => v - b[i]);
  const dot = (a, b) => a.reduce((s, v, i) => s + v * b[i], 0);
  const len = (a) => Math.sqrt(dot(a, a));

  const failures = [];
  const check = (ok, text) => {
    console.log(`  ${ok ? 'OK  ' : 'ПРОВАЛ'}  ${text}`);
    if (!ok) failures.push(text);
  };

  await sleep(8000);
  const box = await wc.executeJavaScript(`(() => {
    const r = document.querySelector('.viewport-host, .center canvas, .center').getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  })()`);
  const cx = Math.round(box.x + box.w / 2);
  const cy = Math.round(box.y + box.h / 2);
  const wheel = async (ticks, up, shift = false) => {
    for (let i = 0; i < ticks; i += 1) {
      // В Electron положительный deltaY колеса — от себя, к приближению.
      wc.sendInputEvent({ type: 'mouseWheel', x: cx, y: cy, deltaX: 0,
                          deltaY: up ? 120 : -120, wheelTicksY: up ? 1 : -1, canScroll: true,
                          modifiers: shift ? ['shift'] : [] });
      await sleep(25);
    }
    await sleep(500);
  };

  const start = await camera();
  if (!start) throw new Error('окно не прислало камеру');
  const axis = sub(start.target, start.position);       // взгляд: от камеры к центру

  const stops = [start];
  for (let i = 0; i < 6; i += 1) {
    await wheel(10, true);
    stops.push(await camera());
  }
  const legs = stops.slice(1).map((c, i) => len(sub(c.position, stops[i].position)));
  const spread = (Math.max(...legs) - Math.min(...legs)) / Math.max(...legs);
  check(spread < 0.03, `шаг колеса постоянный: путь за 10 щелчков ${legs.map((l) => l.toFixed(1)).join(', ')} мм`);
  await wheel(10, true, true);
  const fine = await camera();
  const fineLeg = len(sub(fine.position, stops[stops.length - 1].position));
  check(Math.abs(fineLeg / legs[0] - 0.2) < 0.02,
        `с Shift шаг впятеро мельче: ${fineLeg.toFixed(1)} мм против ${legs[0].toFixed(1)}`);
  const close = fine;
  const ahead = dot(sub(close.position, start.target), axis) / len(axis);
  check(ahead > 1, `камера пролетела за прежний центр вращения на ${ahead.toFixed(1)} мм`);

  const pressH = () => {
    wc.sendInputEvent({ type: 'keyDown', keyCode: 'H' });
    wc.sendInputEvent({ type: 'char', keyCode: 'h' });
    wc.sendInputEvent({ type: 'keyUp', keyCode: 'H' });
  };
  pressH();
  await sleep(300);
  await wheel(80, false);
  const far = await camera();
  const away = len(sub(far.position, far.target));
  check(away > 6000, `отдаление дальше прежней дальней плоскости (5 м): ${away.toFixed(0)} мм`);
  const image = await wc.capturePage();
  if (process.env.DATUM_OUT) fs.writeFileSync(process.env.DATUM_OUT, image.toPNG());
  // Деталь видна и издалека: в центре кадра есть пиксели светлее фона.
  const bitmap = image.toBitmap();
  const { width, height } = image.getSize();
  let lit = 0;
  const scale = width / win.getContentSize()[0];
  for (let y = Math.round(cy * scale) - 20; y < Math.round(cy * scale) + 20; y += 1) {
    for (let x = Math.round(cx * scale) - 20; x < Math.round(cx * scale) + 20; x += 1) {
      if (x < 0 || y < 0 || x >= width || y >= height) continue;
      const i = (y * width + x) * 4;
      if (bitmap[i] + bitmap[i + 1] + bitmap[i + 2] > 3 * 70) lit += 1;
    }
  }
  check(lit > 0, `издалека деталь не обрезана: ${lit} светлых точек в центре кадра`);

  pressH();
  const home = await camera();
  const drift = len(sub(home.position, start.position)) + len(sub(home.target, start.target));
  check(drift < 0.5, `«Вернуть камеру» — туда же, где при открытии (расхождение ${drift.toFixed(3)} мм)`);

  if (failures.length) throw new Error(`не прошло: ${failures.join('; ')}`);
};
