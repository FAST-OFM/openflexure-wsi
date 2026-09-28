import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import SlideScanControls from "../../components/tabContentComponents/slideScanComponents/slideScanControls.vue";

function groupedSettings() {
  return [
    {
      element_type: "container",
      css_class: "fast-ofm-wizard-essential",
      children: [{ element_type: "property_control", property_name: "max_range_um" }],
    },
    {
      element_type: "container",
      css_class: "fast-ofm-wizard-advanced",
      children: [{ element_type: "accordion", title: "Area and path" }],
    },
  ];
}

function computedVm(workflowSettings) {
  const vm = { workflowSettings };
  vm.settingsGroup = SlideScanControls.methods.settingsGroup.bind(vm);
  return vm;
}

describe("compact Fast OFM scan wizard", () => {
  it("separates the daily controls from the engineering controls", () => {
    const vm = computedVm(groupedSettings());

    expect(SlideScanControls.computed.compactWizard.call(vm)).toBe(true);
    expect(SlideScanControls.computed.essentialWorkflowSettings.call(vm)).toEqual(
      groupedSettings()[0].children,
    );
    expect(SlideScanControls.computed.advancedWorkflowSettings.call(vm)).toEqual(
      groupedSettings()[1].children,
    );
  });

  it("keeps the original rendering for workflows without compact groups", () => {
    const vm = computedVm([{ element_type: "accordion", title: "Area" }]);

    expect(SlideScanControls.computed.compactWizard.call(vm)).toBe(false);
    expect(SlideScanControls.computed.essentialWorkflowSettings.call(vm)).toEqual([]);
    expect(SlideScanControls.computed.advancedWorkflowSettings.call(vm)).toEqual([]);
  });

  it("keeps the focus map next to the essential autofocus controls", async () => {
    const wrapper = mount(SlideScanControls, {
      global: {
        mocks: {
          readThingProperty: vi.fn(async (_thing, property) => {
            if (property === "workflow_name") return "fast_ofm_scan_workflow";
            if (property === "workflow_display_names") {
              return { fast_ofm_scan_workflow: "Fast OFM Scan" };
            }
            return true;
          }),
          getThingEndpoint: vi.fn(async (_thing, endpoint) =>
            endpoint === "settings_ui" ? groupedSettings() : [],
          ),
        },
        stubs: {
          ServerSpecifiedInterface: {
            props: ["elements"],
            template: '<div class="workflow-settings"></div>',
          },
          FocusScanSetupStatus: {
            template: '<div class="focus-map-settings"></div>',
          },
          SimpleAccordion: {
            template: '<div class="advanced-settings"><slot /></div>',
          },
          OutputControls: true,
        },
      },
    });
    await flushPromises();

    const focusMap = wrapper.find(".focus-map-settings").element;
    const advanced = wrapper.find(".advanced-settings").element;
    expect(focusMap.compareDocumentPosition(advanced) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(wrapper.find(".advanced-settings .focus-map-settings").exists()).toBe(false);
  });
});
