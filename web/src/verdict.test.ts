import { describe, expect, it } from "vitest";
import { splitLede } from "./pages/Verdict";

describe("splitLede", () => {
  it("takes the first sentence as the lede and keeps the rest in order", () => {
    const { lede, rest } = splitLede("One. Two more. Three.\n\nNext paragraph.");
    expect(lede).toBe("One.");
    expect(rest).toEqual(["Two more. Three.", "Next paragraph."]);
  });

  it("handles a statement of one sentence", () => {
    expect(splitLede("Only this.")).toEqual({ lede: "Only this.", rest: [] });
  });

  it("does not end the lede at an abbreviation, initials or a decimal", () => {
    expect(splitLede("The price, e.g. €40 per pump, is too high. More.").lede).toBe(
      "The price, e.g. €40 per pump, is too high.",
    );
    expect(splitLede("It weighs ShelfSense vs. fridge cameras. More.").lede).toBe(
      "It weighs ShelfSense vs. fridge cameras.",
    );
    expect(splitLede("The U.S. market is 2.8 times larger. More.").lede).toBe(
      "The U.S. market is 2.8 times larger.",
    );
  });

  it("keeps a closing quote with its sentence and reads across a line break", () => {
    const { lede, rest } = splitLede('The plan says "break even in year one."\nThat is unlikely.');
    expect(lede).toBe('The plan says "break even in year one."');
    expect(rest).toEqual(["That is unlikely."]);
  });

  it("ends the lede at an acronym or a lone capital that closes a sentence", () => {
    expect(splitLede("Nothing covers GDPR. More.").lede).toBe("Nothing covers GDPR.");
    expect(splitLede("It depends on closing a Series A. Without pilots, no one invests.").lede).toBe(
      "It depends on closing a Series A.",
    );
    expect(splitLede("There is no Plan B. The team has one bet.").lede).toBe("There is no Plan B.");
  });
});
