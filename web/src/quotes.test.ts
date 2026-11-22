import { describe, expect, it } from "vitest";
import { findQuote } from "./quotes";

const IDEA = `**Solution.** A small battery-powered camera clips inside the fridge door and
photographs the shelves every time the door closes. It costs €149.

**Market.** There are no direct competitors: smart fridges are expensive.`;

const slice = (quote: string) => {
  const span = findQuote(IDEA, quote);
  return span ? IDEA.slice(span.start, span.end) : null;
};

describe("findQuote", () => {
  it("finds a quote across a line break and returns the original text", () => {
    expect(slice("fridge door and photographs the shelves")).toBe(
      "fridge door and\nphotographs the shelves",
    );
  });

  it("ignores Markdown emphasis, quote marks and case", () => {
    expect(slice("**there are no direct competitors**")).toBe("There are no direct competitors");
    expect(slice("'It costs €149.'")).toBe("It costs €149");
  });

  it("keeps positions right after a character that lowercases to two", () => {
    const idea = "We start in İstanbul and Ankara. Customers pay €5 a month.";
    const span = findQuote(idea, "Customers pay €5 a month");
    expect(span && idea.slice(span.start, span.end)).toBe("Customers pay €5 a month");
  });

  it("rejects quotes with words left out or stopping inside a word", () => {
    expect(findQuote(IDEA, "The camera clips inside the fridge door")).toBeNull();
    expect(findQuote(IDEA, "It costs €14")).toBeNull();
    expect(findQuote(IDEA, "")).toBeNull();
  });
});
