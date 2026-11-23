/**
 * Сценарий-доказательство: пока окно открыто, агент делает вызов — и это
 * видно в окне живьём.
 *
 * Агент здесь настоящий: системный `claude` с тем же временным MCP-конфигом,
 * что делает оболочка. Конечная точка берётся у самого окна, потому что файл
 * рукопожатия одноразовый и уже удалён.
 */
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win }) {
  const out = process.env.DATUM_OUT;
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));

  const point = await win.webContents.executeJavaScript('window.datum.endpoint()');
  const port = await win.webContents.executeJavaScript(
    'Number(location.port)');
  console.log(`окно смотрит на порт ${port}, проект ${point.project}`);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'datum-demo-'));
  const handshake = path.join(dir, 'hs.json');
  fs.writeFileSync(handshake, JSON.stringify(
    { port, token: point.token, pid: process.pid, project: point.project }),
    { mode: 0o600 });
  const config = path.join(dir, 'mcp.json');
  fs.writeFileSync(config, JSON.stringify({
    mcpServers: {
      datum: {
        command: path.join(__dirname, '..', '..', '.venv', 'bin', 'python'),
        args: ['-m', 'datum.cli', 'mcp', '--stdio'],
        env: { DATUM_HANDSHAKE: handshake },
      },
    },
  }));

  const env = { ...process.env };
  for (const name of Object.keys(env)) {
    if (name.startsWith('CLAUDE_CODE_') || name === 'CLAUDECODE' ||
        name === 'CLAUDE_PID' || name === 'CLAUDE_EFFORT' ||
        name === 'CLAUDE_BRIDGE_SESSION') delete env[name];
  }
  env.HTTP_PROXY = env.HTTPS_PROXY = 'http://127.0.0.1:10808';
  env.NO_PROXY = '127.0.0.1,localhost,::1';

  console.log('запускаю агента…');
  await new Promise((resolve) => {
    const child = spawn(
      path.join(os.homedir(), '.local', 'bin', 'claude'),
      ['--mcp-config', config,
       '--allowed-tools', 'mcp__datum__part_build,mcp__datum__check_run',
       '-p', 'Собери детали инструментом mcp__datum__part_build, затем прогони ' +
             'mcp__datum__check_run. Ответь одной строкой: сколько проверок прошло.'],
      { cwd: point.project, env, stdio: ['ignore', 'pipe', 'pipe'] });
    let said = '';
    child.stdout.on('data', (d) => { said += d.toString(); });
    child.on('exit', (code) => {
      console.log(`агент ответил (код ${code}): ${said.trim().slice(0, 200)}`);
      resolve();
    });
    setTimeout(() => { child.kill(); resolve(); }, 300000);
  });

  // Открываем «Ленту» в нижней панели, чтобы увидеть результат.
  await win.webContents.executeJavaScript(`
    (() => {
      const tabs = [...document.querySelectorAll('.tabs button')];
      const log = tabs.find(b => b.textContent.trim().startsWith('Лента'));
      if (log) log.click();
      return Boolean(log);
    })()`);

  await sleep(1500);
  fs.writeFileSync(out, (await win.webContents.capturePage()).toPNG());
  console.log(`снимок: ${out}`);
  fs.rmSync(dir, { recursive: true, force: true });
};
