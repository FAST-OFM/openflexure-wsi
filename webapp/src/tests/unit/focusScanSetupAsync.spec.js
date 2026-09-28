import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import FocusSetup from "@/components/tabContentComponents/slideScanComponents/focusScanSetupStatus.vue";

const cap = {
  supported: true,
  available: false,
  reasons: ["TEST_ONLY_no_reference"],
  autofocus_method: "led",
  focus_strategy: "single_autofocus",
  current_binding: null,
  saved_binding_current: null,
  saved_binding_mismatched_fields: [],
};
const disabled = () => FocusSetup.methods.normaliseSettings(undefined, cap);

describe("Independent exact-component async configuration boundary", () => {
  it("ignores an older capability response after a newer selector refresh", async () => {
    const endpoint = vi.fn(async () => cap);
    const wrapper = mount(FocusSetup, {
      props: { workflowName: "snake_workflow" },
      global: {
        mocks: {
          readThingProperty: vi.fn(async () => disabled()),
          getThingEndpoint: endpoint,
          writeThingProperty: vi.fn(),
          modalError: vi.fn(),
        },
      },
    });
    await flushPromises();
    let releaseOld;
    let releaseNew;
    endpoint.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseOld = resolve;
        }),
    );
    endpoint.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseNew = resolve;
        }),
    );
    const old = wrapper.vm.refreshCapability();
    const current = wrapper.vm.refreshCapability();
    releaseNew({ ...cap, autofocus_method: "none" });
    await current;
    releaseOld({ ...cap, autofocus_method: "led" });
    await old;
    expect(wrapper.vm.capability.autofocus_method).toBe("none");
    wrapper.unmount();
  });
  it("refreshes current selector capability without discarding an unfinished numeric draft", async () => {
    let capability = cap;
    const write = vi.fn();
    const wrapper = mount(FocusSetup, {
      props: { workflowName: "snake_workflow" },
      global: {
        mocks: {
          readThingProperty: vi.fn(async () => disabled()),
          getThingEndpoint: vi.fn(async () => capability),
          writeThingProperty: write,
          modalError: vi.fn(),
        },
      },
    });
    await flushPromises();
    wrapper.vm.draft.neighborRadiusUm = "123";
    capability = {
      ...cap,
      autofocus_method: "openflexure",
      reasons: ["TEST_ONLY_selection_changed"],
    };
    await wrapper.vm.refreshCapability();
    expect(wrapper.vm.capability.autofocus_method).toBe("openflexure");
    expect(wrapper.vm.draft.neighborRadiusUm).toBe("123");
    expect(write).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("does not present a failed settings read as a saved disabled setup", async () => {
    const wrapper = mount(FocusSetup, {
      props: { workflowName: "snake_workflow" },
      global: {
        mocks: {
          readThingProperty: vi.fn(async () => null),
          getThingEndpoint: vi.fn(async () => cap),
          writeThingProperty: vi.fn(),
          modalError: vi.fn(),
        },
      },
    });
    await flushPromises();
    expect(wrapper.vm.loading).toBe(false);
    expect(wrapper.vm.draft).toBeNull();
    expect(wrapper.text()).toContain("Saved focus setup could not be read");
    expect(wrapper.text()).not.toContain("Saved readback");
    wrapper.unmount();
  });
  it("does not apply the completion/readback of a previous workflow to the newly selected workflow", async () => {
    const saved = { snake_workflow: disabled(), raster_workflow: disabled() };
    saved.raster_workflow.run.no_tissue_mode = "pause";
    let finishWrite;
    const writeGate = new Promise((resolve) => {
      finishWrite = resolve;
    });
    const read = vi.fn(async (thing, property) =>
      structuredClone(property === "focus_scan_capability" ? cap : saved[thing]),
    );
    const write = vi.fn(async (thing, property, value) => {
      await writeGate;
      saved[thing] = structuredClone(value);
    });
    const wrapper = mount(FocusSetup, {
      props: { workflowName: "snake_workflow" },
      global: {
        mocks: {
          readThingProperty: read,
          getThingEndpoint: vi.fn(async () => cap),
          writeThingProperty: write,
          modalError: vi.fn(),
        },
      },
    });
    await flushPromises();
    wrapper.vm.draft.noTissueMode = "skip_tile";
    const pending = wrapper.vm.applyDraft();
    await flushPromises();
    await wrapper.setProps({ workflowName: "raster_workflow" });
    await flushPromises();
    expect(wrapper.vm.draft.noTissueMode).toBe("pause");
    const callsBeforeCompletion = read.mock.calls.length;
    finishWrite();
    await pending;
    await flushPromises();
    expect(write.mock.calls.map((call) => call[0])).toEqual(["snake_workflow"]);
    expect
      .soft(
        read.mock.calls
          .slice(callsBeforeCompletion)
          .filter((call) => call[1] === "focus_scan")
          .every((call) => call[0] === "snake_workflow"),
      )
      .toBe(true);
    expect.soft(wrapper.vm.draft.noTissueMode).toBe("pause");
    expect.soft(wrapper.vm.applyState).toBe("idle");
    expect.soft(wrapper.vm.applyMessage).toBe("");
    wrapper.unmount();
  });

  it("does not label an already committed PUT as not saved when its readback is unavailable", async () => {
    let wasWritten = false;
    const read = vi.fn(async (_thing, property) =>
      property === "focus_scan_capability" ? cap : wasWritten ? undefined : disabled(),
    );
    const write = vi.fn(async () => {
      wasWritten = true;
    });
    const wrapper = mount(FocusSetup, {
      props: { workflowName: "snake_workflow" },
      global: {
        mocks: {
          readThingProperty: read,
          getThingEndpoint: vi.fn(async () => cap),
          writeThingProperty: write,
          modalError: vi.fn(),
        },
      },
    });
    await flushPromises();
    await wrapper.vm.applyDraft();
    expect(wasWritten).toBe(true);
    expect(write).toHaveBeenCalledOnce();
    expect(wrapper.vm.applyState).not.toBe("saved");
    expect(wrapper.vm.applyMessage).not.toMatch(/^Not saved:/);
    wrapper.unmount();
  });
});
