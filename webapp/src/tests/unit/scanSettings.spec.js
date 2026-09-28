import { shallowMount, flushPromises } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import PropertyControl from "../../components/labThingsComponents/propertyControl.vue";
import ServerSpecifiedInterface from "../../components/labThingsComponents/serverSpecifiedInterface.vue";
import ServerSpecifiedPropertyControl from "../../components/labThingsComponents/serverSpecifiedPropertyControl.vue";
import ScanSettingsModal from "../../components/tabContentComponents/slideScanComponents/scanSettingsModal.vue";
import CompactHelp from "../../components/genericComponents/compactHelp.vue";

describe("scan geometry refresh", () => {
  it("renders long server guidance as compact expandable help", () => {
    const wrapper = shallowMount(ServerSpecifiedInterface, {
      props: {
        elements: [{ element_type: "help_block", label: "Current geometry", text: "Full details" }],
      },
    });
    expect(wrapper.findComponent(CompactHelp).props()).toMatchObject({
      label: "Current geometry",
      text: "Full details",
    });
  });

  it("announces a saved value only after write and rounded readback", async () => {
    const events = [];
    const vm = {
      modelValue: 0,
      readBack: true,
      readBackDelay: 0,
      label: "Scan X span (µm)",
      thingName: "snake_workflow",
      propertyName: "scan_width_um",
      writeThingProperty: vi.fn(async () => events.push("write")),
      readProperty: vi.fn(async () => {
        events.push("read");
        return 2056.5;
      }),
      modalNotify: vi.fn(async () => events.push("rounding")),
      modalError: vi.fn(),
      $emit: vi.fn((name) => events.push(name)),
    };
    await PropertyControl.methods.writeProperty.call(vm, 2000);
    expect(vm.writeThingProperty).toHaveBeenCalledWith("snake_workflow", "scan_width_um", 2000);
    expect(events).toEqual(["write", "read", "rounding", "saved"]);
  });

  it("does not signal success or retry a failed write", async () => {
    const vm = {
      writeThingProperty: vi.fn().mockRejectedValue(new Error("Invalid size")),
      readProperty: vi.fn(),
      modalError: vi.fn(),
      $emit: vi.fn(),
    };
    await PropertyControl.methods.writeProperty.call(vm, -1);
    expect(vm.writeThingProperty).toHaveBeenCalledTimes(1);
    expect(vm.$emit).not.toHaveBeenCalled();
    expect(vm.readProperty).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])(
    "refreshes only when the scan panel opts in (%s)",
    async (refreshOnSaved) => {
      const wrapper = shallowMount(ServerSpecifiedInterface, {
        props: {
          refreshOnSaved,
          elements: [
            {
              element_type: "property_control",
              thing: "snake_workflow",
              property_name: "scan_width_um",
              label: "Scan X span (µm)",
            },
          ],
        },
      });
      wrapper.findComponent(ServerSpecifiedPropertyControl).vm.$emit("saved");
      await flushPromises();
      expect(Boolean(wrapper.emitted("requestUpdate"))).toBe(refreshOnSaved);
      wrapper.unmount();
    },
  );
});

describe("current scan frozen focus settings", () => {
  it("uses readable units and a compact read-only binding section", () => {
    const wrapper = shallowMount(ScanSettingsModal, {
      props: {
        scanDetails: {
          workflow: "SnakeWorkflow",
          settings: {
            focus_scan: {
              run: {
                autofocus_method: "led",
                focus_strategy: "single_autofocus",
                surface: {
                  enabled: true,
                  minimum_plane_points: 4,
                  maximum_neighbors: 12,
                  maximum_candidate_observations: 256,
                  neighbor_radius_um: 80,
                  maximum_observation_age_s: 300,
                  allow_extrapolation: false,
                  maximum_extrapolation_um: 0,
                  maximum_prediction_delta_um: 10,
                  measurement_offset_um: -3,
                  maximum_fit_residual_um: 2,
                  minimum_fit_inlier_fraction: 0.7,
                  maximum_fit_condition_number: 1000,
                  maximum_plane_slope_um_per_um: 0.1,
                },
                unpredicted_focus_mode: "selected_method",
                no_tissue_mode: "keep_z",
                white_search: null,
                binding: {
                  reference_id: "TEST_ONLY_reference",
                  reference_status: "valid",
                  camera_stage_mapping_id: "TEST_ONLY_mapping",
                  rg_focus_model_id: "TEST_ONLY_rg",
                  rg_focus_model_status: "valid",
                  approach_profile_id: "TEST_ONLY_approach",
                  approach_status: "valid",
                  axis_scales: [{ axis: "x", units_per_mm: 1000 }],
                },
              },
              expected_prediction_error_range_um: [-2, 2],
              maximum_field_elapsed_s: 600,
              maximum_field_z_travel_um: 500,
              post_move_verification_reserve_s: 30,
              experiment_envelope: {
                x_um: [-500, 500],
                y_um: [-500, 500],
                z_um: [-50, 50],
              },
            },
          },
        },
      },
    });

    const groups = wrapper.vm.formattedSettings;
    expect(groups.focusCurrent.title).toBe("Focus — Current Frozen Snapshot");
    expect(groups.focusCurrent.items["Autofocus Method"]).toBe("Separate R/G → WHITE fallback");
    expect(groups.focusSupport.items["Neighbour Radius"]).toBe("80 µm");
    expect(groups.focusLimits.items["Maximum Field Elapsed"]).toBe("600 s");
    expect(groups.focusBinding.items.Reference).toBe("TEST_ONLY_reference (valid)");
    expect(groups.focusBinding.items["R/G Model"]).toBe("TEST_ONLY_rg (valid)");
    expect(Object.keys(groups.focusBinding.items)).toHaveLength(4);
    expect(groups.focus_scan).toBeUndefined();
  });
});
