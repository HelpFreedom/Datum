/** Окно запускает Codex с MCP и возвращается к тому же сеансу после перезагрузки. */
const assert = require('node:assert/strict');
const { setTimeout: sleep } = require('node:timers/promises');

async function waitFor(check, purpose) {
  for (let i = 0; i < 120; i++) {
    if (await check()) return;
    await sleep(250);
  }
  assert.fail(purpose);
}

module.exports = async function run({ win }) {
  const wc = win.webContents;
  const js = (source) => wc.executeJavaScript(source);
  const status = () => js('window.datum?.agent.status()');
  const hasText = (text) => js(`document.querySelector('.xterm-rows')?.innerText.includes(${JSON.stringify(text)})`);

  await waitFor(() => js(`Boolean(document.querySelector('.status button.status-item'))`), 'строка состояния');
  await js(`document.querySelector('.status button.status-item').click()`);
  assert.deepEqual(await status(), { running: false, kind: null });
  assert.ok(await js(`(() => {
    const button = [...document.querySelectorAll('.agent-intro button')]
      .find((entry) => entry.innerText.includes('Запустить Codex'));
    button?.click();
    return Boolean(button);
  })()`), 'кнопка Codex');
  await waitFor(async () => (await status()).kind === 'codex', 'запуск Codex');
  await waitFor(() => hasText('CODEX_MCP_OK'), 'инструмент MCP в терминале');

  const reloaded = new Promise((resolve) => wc.once('did-finish-load', resolve));
  wc.reload();
  await reloaded;
  await waitFor(() => hasText('CODEX_MCP_OK'), 'вывод терминала после перезагрузки');
  assert.deepEqual(await status(), { running: true, kind: 'codex' });
  await js(`window.datum.agent.write(${JSON.stringify('input\n')})`);
  await waitFor(() => hasText('эхо: input'), 'ввод в терминал после перезагрузки');
  console.log('Codex в окне: запуск, вызов MCP и восстановление сеанса — ОК.');
};
