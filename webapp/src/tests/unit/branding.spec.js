import { describe, expect, it, vi } from "vitest";
import AppContent from "../../components/appContent.vue";

function navigationState() {
  return AppContent.data.call({ forceStartCalModal: vi.fn() });
}

describe("OpenFlexure attribution", () => {
  it("keeps the About navigation entry", () => {
    const state = navigationState();
    expect([...state.coreTopTabs, ...state.bottomTabs].map((tab) => tab.id)).toContain("about");
    expect([...state.coreTopTabs, ...state.bottomTabs].map((tab) => tab.title)).toContain("About");
  });
});
