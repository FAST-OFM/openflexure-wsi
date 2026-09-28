import { readFileSync, statSync } from "node:fs";
import { describe, it, expect } from "vitest";
import icons from "../../assets/fonts/icons.json";

const sources = import.meta.glob("../../components/**/*.vue", {
  query: "?raw",
  import: "default",
  eager: true,
});

describe("small local icon font", () => {
  it("covers literal icons, tab icons and the known dynamic choices", () => {
    const used = new Set(["check", "content_copy", "more_vert", "sync_alt"]);
    for (const source of Object.values(sources)) {
      for (const match of source.matchAll(/\bicon(?:=|:\s*)"([a-z_]+)"/g)) used.add(match[1]);
      for (const match of source.matchAll(
        /<span\b(?=[^>]*material-symbols-outlined)[^>]*>([\s\S]*?)<\/span>/g,
      )) {
        const text = match[1].trim();
        if (/^[a-z_]+$/.test(text)) used.add(text);
        else for (const literal of text.matchAll(/"([a-z_]+)"/g)) used.add(literal[1]);
      }
    }
    expect(used.size).toBeGreaterThan(25);
    expect([...used].filter((name) => !icons.includes(name))).toEqual([]);
    expect(new Set(icons).size).toBe(icons.length);
  });

  it("ships a bounded-size local WOFF2 and preloads the same asset", () => {
    const font = "src/assets/fonts/material-symbols-outlined.woff2";
    expect(statSync(font).size).toBeLessThan(100_000);
    expect(readFileSync(font).subarray(0, 4).toString()).toBe("wOF2");
    const css = readFileSync("src/assets/fonts/icons.css", "utf8");
    expect(css).toContain("./material-symbols-outlined.woff2");
    expect(css).toContain("width: 1em");
    expect(css).toContain("overflow: hidden");
    expect(css).not.toMatch(/https?:\/\//);
    expect(readFileSync("index.html", "utf8")).toContain("/" + font);
    expect(readFileSync("src/main.js", "utf8")).not.toContain("material-symbols/outlined.css");
  });
});
