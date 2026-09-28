import { shallowMount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { afterEach, describe, expect, it, vi } from "vitest";
import SettingsContent from "../../components/tabContentComponents/settingsContent.vue";
import { useSettingsStore } from "../../stores/settings.js";

let wrapper;

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  window.history.replaceState(null, "", "#");
  // Happy DOM schedules an internal browser-error-capture task for History
  // URL changes. It is not an application timer and must not escape the test.
  window.happyDOM.cancelAsync();
});

function mountSettings(hash = "#settings/camera-preview") {
  window.history.replaceState(null, "", hash);
  wrapper = shallowMount(SettingsContent, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: false })],
      mocks: { thingAvailable: () => true },
    },
  });
  return wrapper;
}

describe("grouped settings navigation", () => {
  it("groups operator settings and restores a deep-linked page", () => {
    const view = mountSettings();
    expect(view.vm.visibleGroups.map((group) => group.title)).toEqual([
      "Application",
      "Control",
      "Camera",
      "Stage",
    ]);
    expect(view.vm.currentTab).toBe("camera-preview");
    expect(view.vm.allTabs.map((tab) => tab.title)).toEqual(
      expect.arrayContaining(["WHITE calibration", "Z backlash"]),
    );
  });

  it("persists the selected page and updates the URL without navigation", () => {
    const view = mountSettings("#settings/display");
    const preventDefault = vi.fn();
    view.vm.setTab({ preventDefault }, "camera-autofocus");
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(view.vm.currentTab).toBe("camera-autofocus");
    expect(useSettingsStore().settingsPage).toBe("camera-autofocus");
    expect(window.location.hash).toBe("#settings/camera-autofocus");
  });
});
