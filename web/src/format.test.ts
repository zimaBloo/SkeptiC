import { describe, expect, it } from "vitest";
import { countWord, modelName } from "./format";

describe("model names", () => {
  it("names the local models", () => {
    expect(modelName("ollama/qwen3.5:9b-q4_K_M")).toBe("Qwen 3.5 9B");
    expect(modelName("ollama/gemma4:12b-it-qat")).toBe("Gemma 4 12B");
    expect(modelName("ollama/ministral-3:8b-instruct-2512-q4_K_M")).toBe("Ministral 3 8B");
    expect(modelName("ollama/nemotron-3-nano:4b-q4_K_M")).toBe("Nemotron 3 Nano 4B");
  });

  it("names the frontier models without their provider path", () => {
    expect(modelName("openrouter/openai/gpt-6.2-sol")).toBe("GPT-6.2 Sol");
    expect(modelName("openrouter/google/gemini-4-carbon")).toBe("Gemini 4 Carbon");
    expect(modelName("openrouter/anthropic/claude-sonnet-5.5")).toBe("Claude Sonnet 5.5");
    expect(modelName("openrouter/x-ai/grok-4.7")).toBe("Grok 4.7");
    expect(modelName("openrouter/anthropic/claude-opus-5.5")).toBe("Claude Opus 5.5");
  });

  it("names other GPT and Claude versions without cutting them short", () => {
    expect(modelName("openai/gpt-4o")).toBe("GPT-4o");
    expect(modelName("openai/gpt-4o-mini")).toBe("GPT-4o Mini");
    expect(modelName("anthropic/claude-sonnet-4-5")).toBe("Claude Sonnet 4.5");
    expect(modelName("anthropic/claude-sonnet-4-20250514")).toBe("Claude Sonnet 4");
    expect(modelName("openrouter/anthropic/claude-haiku-5.5")).toBe("Claude Haiku 5.5");
    expect(modelName("openrouter/openai/gpt-6-luna")).toBe("GPT-6 Luna");
  });

  it("shortens names it does not know", () => {
    expect(modelName("openrouter/deepseek/deepseek-v4-pro")).toBe("deepseek-v4-pro");
    expect(modelName("ollama/llama3:8b")).toBe("llama3");
    expect(modelName(null)).toBe("");
  });
});

describe("counts in words", () => {
  it("spells out small numbers", () => {
    expect(countWord(4)).toBe("four");
    expect(countWord(5)).toBe("five");
    expect(countWord(12)).toBe("12");
  });
});
