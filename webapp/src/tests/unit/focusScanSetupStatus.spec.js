import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import FocusScanSetupStatus from "../../components/tabContentComponents/slideScanComponents/focusScanSetupStatus.vue";

function disabledSettings(method = "led", strategy = "single_autofocus") {
  return {
    run: {
      autofocus_method: method,
      focus_strategy: strategy,
      surface: {
        enabled: false,
        minimum_plane_points: 4,
        maximum_neighbors: 12,
        maximum_candidate_observations: 256,
        neighbor_radius_um: null,
        allow_extrapolation: false,
        maximum_extrapolation_um: null,
        maximum_prediction_delta_um: null,
        measurement_offset_um: null,
        maximum_fit_residual_um: null,
        minimum_fit_inlier_fraction: null,
        maximum_fit_condition_number: null,
        maximum_plane_slope_um_per_um: null,
        maximum_observation_age_s: null,
      },
      unpredicted_focus_mode: "selected_method",
      white_search: null,
      no_tissue_mode: "keep_z",
      on_focus_failure: "pause",
      binding: null,
    },
    expected_prediction_error_range_um: null,
    maximum_field_elapsed_s: null,
    maximum_field_z_travel_um: null,
    post_move_verification_reserve_s: null,
    experiment_envelope: null,
  };
}

const testOnlyBinding = {
  stage_controller_id: "TEST_ONLY_stage",
  reference_id: "TEST_ONLY_reference",
  reference_status: "valid",
  axis_scales: [
    { axis: "x", units_per_mm: 1000, direction_sign: 1 },
    { axis: "y", units_per_mm: 1000, direction_sign: 1 },
    { axis: "z", units_per_mm: 1000, direction_sign: 1 },
  ],
  camera_stage_mapping_id: "TEST_ONLY_mapping",
  camera_stage_mapping_status: "valid",
  geometry_id: "TEST_ONLY_geometry",
  rg_focus_model_id: "TEST_ONLY_rg",
  rg_focus_model_status: "valid",
  red_flat_field_profile_id: "TEST_ONLY_red",
  red_flat_field_status: "valid",
  green_flat_field_profile_id: "TEST_ONLY_green",
  green_flat_field_status: "valid",
  approach_profile_id: "TEST_ONLY_approach",
  approach_status: "valid",
  approach_parameters: { preload_um: 8, approach_sign: 1 },
};

function capability(overrides = {}) {
  return {
    supported: true,
    available: true,
    reasons: [],
    autofocus_method: "led",
    focus_strategy: "single_autofocus",
    fast_ofm_background_skipping: false,
    current_binding: testOnlyBinding,
    saved_binding_current: null,
    saved_binding_reason: null,
    saved_binding_mismatched_fields: [],
    on_focus_failure: "pause",
    maximum_white_searches_per_field: 1,
    ...overrides,
  };
}

function fillValidDraft(vm, { white = false } = {}) {
  Object.assign(vm.draft, {
    enabled: true,
    unpredictedFocusMode: white ? "white_then_led" : "selected_method",
    noTissueMode: "keep_z",
    allowExtrapolation: false,
    minimumPlanePoints: "4",
    maximumNeighbors: "4",
    maximumCandidateObservations: "32",
    neighborRadiusUm: "80",
    maximumObservationAgeS: "300",
    maximumExtrapolationUm: "0",
    maximumPredictionDeltaUm: "10",
    measurementOffsetUm: "-3",
    maximumFitResidualUm: "2",
    minimumFitInlierFraction: "0.7",
    maximumFitConditionNumber: "1000",
    maximumPlaneSlopeUmPerUm: "0.1",
    predictionErrorLowUm: "-2",
    predictionErrorHighUm: "2",
    envelopeXLowUm: "-500",
    envelopeXHighUm: "500",
    envelopeYLowUm: "-500",
    envelopeYHighUm: "500",
    envelopeZLowUm: "-50",
    envelopeZHighUm: "50",
    maximumFieldElapsedS: "600",
    maximumFieldZTravelUm: "500",
    postMoveVerificationReserveS: "30",
    whiteRangeLowUm: white ? "-12" : "",
    whiteRangeHighUm: white ? "8" : "",
    whiteSearchTimeoutS: white ? "300" : "",
    totalFocusBudgetS: white ? "500" : "",
    approachAndRgReserveS: white ? "120" : "",
    maximumWhiteLedDisagreementUm: white ? "4" : "",
  });
}

async function setupWrapper({ read, write = vi.fn(), cap = capability() } = {}) {
  const saved = disabledSettings();
  const reader = read || vi.fn(async () => saved);
  const endpoint = vi.fn(async () => cap);
  const modalError = vi.fn();
  const wrapper = mount(FocusScanSetupStatus, {
    props: { workflowName: "snake_workflow" },
    global: {
      mocks: {
        readThingProperty: reader,
        getThingEndpoint: endpoint,
        writeThingProperty: write,
        modalError,
      },
    },
  });
  await flushPromises();
  return { wrapper, reader, endpoint, write, modalError };
}

describe("focus scan next-run setup", () => {
  it("offers one switch for the map and joint preload with the proven bounded preset", async () => {
    const { wrapper } = await setupWrapper();

    expect(wrapper.text()).toContain("Use focus map + joint XY/Z preload");
    wrapper.vm.draft.enabled = true;
    wrapper.vm.enabledChanged();

    const payload = wrapper.vm.buildPayload();
    expect(payload.run.surface.enabled).toBe(true);
    expect(payload.run.surface.measurement_offset_um).toBe(-3);
    expect(payload.run.unpredicted_focus_mode).toBe("selected_method");
    expect(payload.expected_prediction_error_range_um).toEqual([-4, 4]);
    expect(payload.experiment_envelope).toEqual({
      x_um: [-50, 1000],
      y_um: [-1000, 50],
      z_um: [-50, 50],
    });
  });

  it("turns acceleration off without erasing the saved advanced values", async () => {
    const { wrapper } = await setupWrapper();
    wrapper.vm.draft.enabled = true;
    wrapper.vm.enabledChanged();
    wrapper.vm.draft.neighborRadiusUm = "777";
    wrapper.vm.draft.unpredictedFocusMode = "white_then_led";

    wrapper.vm.draft.enabled = false;
    wrapper.vm.enabledChanged();

    expect(wrapper.vm.draft.neighborRadiusUm).toBe("777");
    expect(wrapper.vm.draft.unpredictedFocusMode).toBe("selected_method");
    expect(wrapper.vm.buildPayload().run.surface.enabled).toBe(false);
  });

  it("renders every canonical operational field with units and read-only identities", async () => {
    const { wrapper, write } = await setupWrapper();
    const text = wrapper.text();
    for (const label of [
      "Minimum plane points",
      "Maximum neighbours",
      "Maximum candidate observations",
      "Neighbour radius (µm)",
      "Maximum observation age (s)",
      "Maximum extrapolation (µm)",
      "Maximum prediction delta (µm)",
      "R/G measurement offset (µm)",
      "Maximum fit residual (µm)",
      "Minimum fit inlier fraction (0–1)",
      "Maximum fit condition number",
      "Maximum plane slope (µm/µm)",
      "Expected prediction error lower (µm)",
      "Expected prediction error upper (µm)",
      "Envelope X lower (µm)",
      "Envelope X upper (µm)",
      "Envelope Y lower (µm)",
      "Envelope Y upper (µm)",
      "Envelope Z lower (µm)",
      "Envelope Z upper (µm)",
      "Maximum field elapsed (s)",
      "Maximum field Z travel (µm)",
      "Post-move verification reserve (s)",
      "WHITE search lower (µm)",
      "WHITE search upper (µm)",
      "WHITE search timeout (s)",
      "Total WHITE-to-R/G budget (s)",
      "Approach and R/G reserve (s)",
      "Maximum WHITE/R/G disagreement (µm)",
    ]) {
      expect(text).toContain(label);
    }
    expect(text).toContain("Pause (fixed)");
    expect(text).toContain("1 (fixed invariant)");
    expect(text).toContain("TEST_ONLY_reference (valid)");
    expect(text).toContain("TEST_ONLY_approach (valid)");
    expect(wrapper.find("textarea").exists()).toBe(false);
    expect(
      wrapper
        .findAll("input")
        .some((input) => Object.values(testOnlyBinding).includes(input.element.value)),
    ).toBe(false);
    expect(write).not.toHaveBeenCalled();
  });

  it("builds one complete typed WHITE payload and never converts blanks to zero", async () => {
    const { wrapper, write, modalError } = await setupWrapper();
    fillValidDraft(wrapper.vm, { white: true });
    const payload = wrapper.vm.buildPayload();
    expect(payload.run.binding).toEqual(testOnlyBinding);
    expect(payload.run.on_focus_failure).toBe("pause");
    expect(payload.run.white_search.maximum_white_searches_per_field).toBe(1);
    expect(payload.run.surface.measurement_offset_um).toBe(-3);
    expect(payload.expected_prediction_error_range_um).toEqual([-2, 2]);

    wrapper.vm.draft.neighborRadiusUm = "";
    await wrapper.vm.applyDraft();
    expect(write).not.toHaveBeenCalled();
    expect(wrapper.vm.applyState).toBe("failed");
    expect(modalError).toHaveBeenCalledOnce();
  });

  it.each([
    [true, "must be a number"],
    ["NaN", "finite number"],
    ["not-a-number", "finite number"],
  ])("rejects non-numeric draft value %s", async (value, message) => {
    const { wrapper } = await setupWrapper();
    fillValidDraft(wrapper.vm);
    wrapper.vm.draft.maximumFieldElapsedS = value;
    expect(() => wrapper.vm.buildPayload()).toThrow(message);
  });

  it("performs one PUT, verifies readback, and does not alter autofocus selectors", async () => {
    let saved = disabledSettings();
    const read = vi.fn(async (_thing, property) =>
      property === "focus_scan_capability" ? capability() : saved,
    );
    const write = vi.fn(async (thing, property, payload) => {
      expect(thing).toBe("snake_workflow");
      expect(property).toBe("focus_scan");
      saved = JSON.parse(JSON.stringify(payload));
    });
    const { wrapper } = await setupWrapper({ read, write });
    fillValidDraft(wrapper.vm);
    await wrapper.vm.applyDraft();
    await flushPromises();

    expect(write).toHaveBeenCalledOnce();
    expect(wrapper.vm.applyState).toBe("saved");
    expect(wrapper.vm.savedSettings.run.autofocus_method).toBe("led");
    expect(wrapper.vm.savedSettings.run.focus_strategy).toBe("single_autofocus");
    expect(wrapper.vm.savedSettings.run.surface.enabled).toBe(true);
  });

  it("does not retry or claim success after a failed Apply", async () => {
    const write = vi.fn().mockRejectedValue(new Error("server rejected test-only payload"));
    const { wrapper, reader, modalError } = await setupWrapper({ write });
    fillValidDraft(wrapper.vm);
    const readsBefore = reader.mock.calls.length;
    await wrapper.vm.applyDraft();

    expect(write).toHaveBeenCalledOnce();
    expect(reader).toHaveBeenCalledTimes(readsBefore);
    expect(wrapper.vm.applyState).toBe("failed");
    expect(wrapper.vm.applyMessage).toContain("Save failed");
    expect(modalError).toHaveBeenCalledOnce();
  });

  it("disables and resets with one focus-setting write even when binding is unavailable", async () => {
    let saved = disabledSettings();
    saved.run.surface.enabled = true;
    saved.run.binding = testOnlyBinding;
    const unavailable = capability({
      available: false,
      reasons: ["A valid live stage reference is required"],
      current_binding: null,
      saved_binding_current: false,
    });
    const read = vi.fn(async (_thing, property) =>
      property === "focus_scan_capability" ? unavailable : saved,
    );
    const writes = [];
    const write = vi.fn(async (_thing, property, payload) => {
      expect(property).toBe("focus_scan");
      saved = JSON.parse(JSON.stringify(payload));
      writes.push(saved);
    });
    const { wrapper } = await setupWrapper({ read, write, cap: unavailable });
    await wrapper.vm.disableNextScan();
    await wrapper.vm.resetNextScan();

    expect(write).toHaveBeenCalledTimes(2);
    expect(writes[0].run.surface.enabled).toBe(false);
    expect(writes[0].run.binding).toBeNull();
    expect(writes[0].run.unpredicted_focus_mode).toBe("selected_method");
    expect(writes[0].run.white_search).toBeNull();
    expect(writes[1]).toEqual(disabledSettings());
  });

  it("loads a workflow switch without carrying the previous browser draft", async () => {
    const read = vi.fn(async (thing, property) => {
      if (property === "focus_scan_capability") return capability();
      const settings = disabledSettings();
      settings.run.no_tissue_mode = thing === "raster_workflow" ? "pause" : "keep_z";
      return settings;
    });
    const { wrapper, write } = await setupWrapper({ read });
    wrapper.vm.draft.neighborRadiusUm = "999";
    await wrapper.setProps({ workflowName: "raster_workflow" });
    await flushPromises();

    expect(wrapper.vm.draft.noTissueMode).toBe("pause");
    expect(wrapper.vm.draft.neighborRadiusUm).toBe("");
    expect(write).not.toHaveBeenCalled();
  });
});

describe("focus scan live status", () => {
  it.each(["no_tissue", "failed", "cancelled", "unknown", "focused", "captured"])(
    "renders the truthful %s outcome from the existing poll payload",
    (outcome) => {
      const wrapper = mount(FocusScanSetupStatus, {
        props: {
          mode: "live",
          focus: {
            scan_state: "running",
            outcome,
            path: "white_then_rg",
            stage: outcome,
            predicted_z_um: 1,
            measured_z_um: 2,
            final_z_um: outcome === "captured" ? 3 : null,
            white_focus_z_um: 1.5,
            white_search_count: 1,
            reason: `TEST_ONLY ${outcome}`,
            counters: {
              fields_planned: 1,
              fields_no_tissue: outcome === "no_tissue" ? 1 : 0,
              fields_focused: ["focused", "captured"].includes(outcome) ? 1 : 0,
              fields_captured: outcome === "captured" ? 1 : 0,
              fields_failed: outcome === "failed" ? 1 : 0,
              fields_cancelled: outcome === "cancelled" ? 1 : 0,
              fields_unknown: outcome === "unknown" ? 1 : 0,
              white_searches_completed: 1,
            },
          },
        },
      });
      expect(wrapper.text()).toContain(outcome.replace("_", " "));
      expect(wrapper.text()).toContain("WHITE → R/G");
      expect(wrapper.text()).toContain(`TEST_ONLY ${outcome}`);
      expect(wrapper.text()).toContain("WHITE searches this field1 / 1 fixed");
    },
  );
});
