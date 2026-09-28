import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { describe, it, expect, vi, afterEach } from "vitest";
import AutofocusControl from "../../components/tabContentComponents/controlComponents/autofocusControl.vue";
import ActionButton from "../../components/labThingsComponents/actionButton.vue";
import { useSettingsStore } from "../../stores/settings.js";
import CompactHelp from "../../components/genericComponents/compactHelp.vue";

let wrapper;
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
});
const profile = (unitsPerMm = 1000, maxMoveMm = 0.05) => ({
  allow_motion: true,
  axes: { z: { enabled: true, units_per_mm: unitsPerMm, max_move_mm: maxMoveMm } },
});
async function mount({
  hardware = profile(),
  stage = "physical",
  saved = {},
  savedMethod = {},
  read,
  rgReady = true,
  simultaneousReady = true,
} = {}) {
  const propertyRead =
    read ||
    vi.fn().mockImplementation((thing, property) => {
      if (thing === "rg_focus" && property === "readiness") {
        return Promise.resolve({ autofocus_ready: rgReady });
      }
      if (thing === "rg_simultaneous" && property === "autofocus_readiness") {
        return Promise.resolve({ autofocus_ready: simultaneousReady });
      }
      return Promise.resolve(hardware);
    });
  wrapper = shallowMount(AutofocusControl, {
    global: {
      plugins: [
        createTestingPinia({
          createSpy: vi.fn,
          initialState: {
            settings: {
              ready: true,
              baseUri: "http://pi/api/v3",
              manualAutofocusRangesUm: saved,
              manualAutofocusMethods: savedMethod,
            },
          },
        }),
      ],
      mocks: {
        thingDescription: (thing) => {
          if (thing === "rg_focus") {
            return { actions: { autofocus_with_white_fallback: {} } };
          }
          if (thing === "rg_simultaneous") {
            return { actions: { autofocus: {} } };
          }
          if (thing !== "stage" || stage === "loading") return undefined;
          return {
            properties: stage === "physical" ? { hardware_settings: {} } : { position: {} },
          };
        },
        readThingProperty: propertyRead,
        modalError: vi.fn(),
      },
    },
  });
  await flushPromises();
  return wrapper.findComponent(ActionButton);
}

describe("manual autofocus physical range", () => {
  it("keeps the range labelled and moves routine explanation into a tooltip", async () => {
    const button = await mount();
    expect(wrapper.find(".menu-subheading").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Total sweep");
    expect(wrapper.get("#manual-autofocus-range").attributes("aria-label")).toBe(
      "Autofocus Z search range (µm)",
    );
    expect(wrapper.get(".autofocus-range").attributes("title")).toContain("±25 µm");
    expect(button.props("submitLabel")).toBe("Autofocus");
    await wrapper.get("#manual-autofocus-range").setValue(0);
    expect(wrapper.findComponent(CompactHelp).props("text")).toContain("Enter a positive range");
    expect(button.props("isDisabled")).toBe(true);
  });

  it.each([1000, 2000, 10000])("uses 50 µm, not 2000 native units (%s units/mm)", async (units) => {
    const button = await mount({ hardware: profile(units) });
    expect(button.props("submitData")).toEqual({ dz: 0.05 * units, start: "centre" });
    expect(button.props("isDisabled")).toBe(false);
    expect(button.props("submitOnEvent")).toBe("globalFastAutofocusEvent");
    expect(wrapper.get("#manual-autofocus-range").element.value).toBe("50");
    expect(wrapper.vm.readThingProperty).toHaveBeenCalledWith("stage", "hardware_settings", true);
  });

  it("keeps range per microscope, independent from scan and jog preferences", async () => {
    const button = await mount();
    await wrapper.get("#manual-autofocus-range").setValue("30");
    expect(button.props("submitData").dz).toBe(30);
    expect(useSettingsStore().manualAutofocusRangesUm["http://pi/api/v3"]).toBe(30);
    useSettingsStore().baseUri = "http://other/api/v3";
    await flushPromises();
    expect(wrapper.vm.rangeUm).toBe(50);
    expect(button.props("submitData").dz).toBe(50);
  });

  it.each([null, {}, { axes: { z: {} } }, profile(0), profile(1000, 0)])(
    "disables an unknown/invalid profile without stock fallback: %j",
    async (hardware) => {
      const button = await mount({ hardware });
      expect(button.props("isDisabled")).toBe(true);
      expect(button.props("submitData").dz).not.toBe(2000);
    },
  );

  it.each([0, -1, 60, "", null, NaN, Infinity])(
    "rejects invalid stored range %s",
    async (range) => {
      const button = await mount({ saved: { "http://pi/api/v3": range } });
      expect(button.props("isDisabled")).toBe(true);
    },
  );

  it("respects smaller configured single-move limits", async () => {
    const button = await mount({ hardware: profile(1000, 0.02) });
    expect(wrapper.vm.rangeUm).toBe(20);
    expect(button.props("submitData").dz).toBe(20);
    expect(button.props("isDisabled")).toBe(false);
  });

  it("waits for metadata and never silently uses native-stage defaults", async () => {
    const button = await mount({ stage: "loading" });
    expect(button.props("isDisabled")).toBe(true);
    expect(button.props("submitData").dz).not.toBe(2000);
  });

  it("retains upstream behaviour only on an identified native stage", async () => {
    const button = await mount({ stage: "native" });
    expect(button.props("submitData").dz).toBe(2000);
    expect(button.props("isDisabled")).toBe(false);
    expect(wrapper.find("#manual-autofocus-range").exists()).toBe(false);
  });

  it("disables launch during reconnect and discards stale profile responses", async () => {
    let finish;
    const read = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValue(profile(2000));
    const button = await mount({ read });
    expect(button.props("isDisabled")).toBe(true);
    useSettingsStore().baseUri = "http://other/api/v3";
    await flushPromises();
    finish(profile(1000));
    await flushPromises();
    expect(button.props("submitData").dz).toBe(100);
    useSettingsStore().ready = false;
    await flushPromises();
    expect(button.props("isDisabled")).toBe(true);
  });

  it("locks range while running but keeps cancellation available", async () => {
    const button = await mount();
    button.vm.$emit("taskStarted");
    await flushPromises();
    expect(wrapper.get("#manual-autofocus-range").attributes("disabled")).toBeDefined();
    expect(button.props("isDisabled")).toBe(false);
    button.vm.$emit("finished");
    await flushPromises();
    expect(wrapper.get("#manual-autofocus-range").attributes("disabled")).toBeUndefined();
  });
});

describe("manual autofocus method", () => {
  it("ignores an invalid persisted method and retains the standard action", async () => {
    const button = await mount({ savedMethod: { "http://pi/api/v3": "unknown" } });
    expect(wrapper.get("#manual-autofocus-method").element.value).toBe("openflexure");
    expect(button.props()).toMatchObject({ thing: "autofocus", action: "fast_autofocus" });
  });

  it("puts the selector below the button and switches to the R/G action", async () => {
    const button = await mount();
    const selector = wrapper.get("#manual-autofocus-method");
    expect(
      button.element.compareDocumentPosition(selector.element) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(button.props()).toMatchObject({
      thing: "autofocus",
      action: "fast_autofocus",
      submitLabel: "Autofocus",
    });

    await selector.setValue("led");
    await flushPromises();
    expect(selector.text()).toContain("R/G + fallback");
    expect(button.props()).toMatchObject({
      thing: "rg_focus",
      action: "autofocus_with_white_fallback",
      submitData: { prepared: true, white_dz: null },
      isDisabled: false,
    });
    expect(wrapper.find("#manual-autofocus-range").exists()).toBe(false);
    expect(useSettingsStore().manualAutofocusMethods["http://pi/api/v3"]).toBe("led");
  });

  it("fails closed when R/G autofocus is not ready", async () => {
    const read = vi.fn().mockImplementation((thing, property) =>
      thing === "rg_focus" && property === "readiness"
        ? Promise.resolve({
            autofocus_ready: false,
            mismatches: [{ code: "control_contract_mismatch" }],
          })
        : Promise.resolve(profile()),
    );
    const button = await mount({ savedMethod: { "http://pi/api/v3": "led" }, read });
    expect(button.props("isDisabled")).toBe(true);
    expect(wrapper.findComponent(CompactHelp).props()).toMatchObject({
      label: "R/G settings changed",
      text: expect.stringContaining("saved R/G calibration is valid"),
    });
  });

  it("selects the isolated simultaneous action only when its own profile is ready", async () => {
    const button = await mount({
      savedMethod: { "http://pi/api/v3": "simultaneous_rg" },
      simultaneousReady: true,
    });
    expect(wrapper.get("#manual-autofocus-method").element.value).toBe("simultaneous_rg");
    expect(button.props()).toMatchObject({
      thing: "rg_simultaneous",
      action: "autofocus",
      submitData: { prepared: true, white_dz: null },
      isDisabled: false,
    });
    wrapper.unmount();
    wrapper = null;

    const read = vi.fn().mockImplementation((thing, property) => {
      if (thing === "rg_simultaneous" && property === "autofocus_readiness") {
        return Promise.resolve({ autofocus_ready: false, reason: "Profile disabled" });
      }
      return Promise.resolve(profile());
    });
    const refused = await mount({
      savedMethod: { "http://pi/api/v3": "simultaneous_rg" },
      read,
    });
    expect(refused.props("isDisabled")).toBe(true);
    expect(wrapper.findComponent(CompactHelp).props()).toMatchObject({
      label: "Simultaneous R/G focus unavailable",
      text: "Profile disabled",
    });
  });
});

it("permits a larger AF range only when the shared single-move limit is explicitly off", async () => {
  const hardware = profile();
  hardware.axes.z.single_move_limit_enabled = false;
  const button = await mount({ hardware, saved: { "http://pi/api/v3": 100 } });
  expect(button.props("isDisabled")).toBe(false);
  expect(button.props("submitData").dz).toBe(100);
  expect(wrapper.vm.limitUm).toBeUndefined();
});
