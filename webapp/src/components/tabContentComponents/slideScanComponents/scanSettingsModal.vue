<template>
  <div id="scan-settings-modal" ref="modalEl" uk-modal>
    <div class="uk-modal-dialog uk-modal-body modal-settings">
      <button class="uk-modal-close-default" type="button" uk-close></button>
      <h2 class="uk-modal-title uk-margin-small-bottom">Current Scan Settings — Frozen at Start</h2>

      <p
        v-if="scanDetails && scanDetails.workflow"
        class="uk-text-meta uk-margin-remove-top uk-margin-bottom"
      >
        Workflow: <strong>{{ scanDetails.workflow }}</strong>
      </p>

      <div v-if="formattedSettings" class="settings-grid">
        <section
          v-for="(group, groupKey) in formattedSettings"
          :key="groupKey"
          class="settings-card"
        >
          <h4 class="card-title">{{ group.title }}</h4>
          <dl class="settings-list">
            <div v-for="(val, label) in group.items" :key="label" class="settings-item">
              <dt>{{ label }}</dt>
              <dd>{{ val }}</dd>
            </div>
          </dl>
        </section>
      </div>

      <p v-else class="uk-text-muted uk-margin-top">Scan settings aren't available yet.</p>
    </div>
  </div>
</template>

<script>
import UIkit from "uikit";
import { formatInfoValue, formatKey, flattenGroup } from "@/js_utils/formatter.mjs";

export default {
  name: "ScanSettingsModal",

  props: {
    scanDetails: {
      type: Object,
      default: null,
    },
  },

  computed: {
    /**
     * Returns the scan settings grouped and formatted for display.
     */
    formattedSettings() {
      return this.formatScanSettings(this.scanDetails);
    },
  },

  methods: {
    open() {
      UIkit.modal(this.$refs.modalEl).show();
    },

    close() {
      UIkit.modal(this.$refs.modalEl).hide();
    },
    /**
     * Convert raw scan settings into grouped display sections.
     *
     * The scan returns settings in a few places:
     * - `settings`: general scan parameters
     * - `stitching_settings`: stitching/output settings
     * - `save_resolution`: resolution of captured images
     *
     * These are normalised into sections containing a title and display rows.
     */
    formatScanSettings(scanDetails) {
      if (!scanDetails) return null;

      // Link all sections together.
      const sections = {
        ...this.formatFocusSettings(scanDetails.settings?.focus_scan),
        ...this.formatGeneralSettings(scanDetails.settings),
        ...this.formatOutputSettings(scanDetails),
      };

      // Avoid rendering an empty settings panel when no data was available.
      return Object.keys(sections).length ? sections : null;
    },

    /**
     * Format the general scan settings section.
     *
     * Simple values become normal key/value rows. Nested objects are kept separate
     * and flattened later so their parent context is preserved in the label.
     */
    formatGeneralSettings(settings) {
      if (!settings) return {};

      const general = {};
      const nested = {};

      Object.entries(settings).forEach(([key, value]) => {
        if (key === "focus_scan") return;
        const isObject = value !== null && typeof value === "object" && !Array.isArray(value);

        if (isObject) {
          nested[key] = value;
        } else {
          general[formatKey(key)] = formatInfoValue(value);
        }
      });

      const sections = {};

      if (Object.keys(general).length) {
        sections.general = {
          title: "General Settings",
          items: general,
        };
      }

      Object.entries(nested).forEach(([key, value]) => {
        sections[key] = {
          title: formatKey(key),
          items: flattenGroup(value),
        };
      });

      return sections;
    },

    /** Format the frozen focus snapshot without exposing a writable binding blob. */
    formatFocusSettings(focusScan) {
      if (!focusScan?.run?.surface) return {};
      const run = focusScan.run;
      const surface = run.surface;
      const white = run.white_search;
      const envelope = focusScan.experiment_envelope;
      const value = (entry, unit = "") =>
        entry === null || entry === undefined
          ? "Not set"
          : `${formatInfoValue(entry)}${unit ? ` ${unit}` : ""}`;
      const range = (entry, unit) =>
        Array.isArray(entry) && entry.length === 2
          ? `${entry[0]} to ${entry[1]} ${unit}`
          : "Not set";
      const autofocusMethod =
        {
          none: "Disabled",
          openflexure: "OpenFlexure (WHITE)",
          led: "Separate R/G → WHITE fallback",
          simultaneous_rg: "Simultaneous R+G → separate R/G → WHITE fallback",
        }[run.autofocus_method] || formatInfoValue(run.autofocus_method);

      const sections = {
        focusCurrent: {
          title: "Focus — Current Frozen Snapshot",
          items: {
            Enabled: surface.enabled ? "Yes" : "No",
            "Autofocus Method": autofocusMethod,
            "Focus Strategy": formatInfoValue(run.focus_strategy),
            "Missing Prediction": formatInfoValue(run.unpredicted_focus_mode),
            "No Tissue": formatInfoValue(run.no_tissue_mode),
            "Focus Failure": "Pause (fixed)",
            "WHITE Searches per Field": "1 (fixed)",
          },
        },
        focusSupport: {
          title: "Focus Map & Prediction Gates",
          items: {
            "Minimum Plane Points": value(surface.minimum_plane_points),
            "Maximum Neighbours": value(surface.maximum_neighbors),
            "Maximum Candidate Observations": value(surface.maximum_candidate_observations),
            "Neighbour Radius": value(surface.neighbor_radius_um, "µm"),
            "Maximum Observation Age": value(surface.maximum_observation_age_s, "s"),
            "Allow Extrapolation": surface.allow_extrapolation ? "Yes" : "No",
            "Maximum Extrapolation": value(surface.maximum_extrapolation_um, "µm"),
            "Maximum Prediction Delta": value(surface.maximum_prediction_delta_um, "µm"),
            "Measurement Offset": value(surface.measurement_offset_um, "µm"),
            "Maximum Fit Residual": value(surface.maximum_fit_residual_um, "µm"),
            "Minimum Fit Inlier Fraction": value(surface.minimum_fit_inlier_fraction),
            "Maximum Fit Condition Number": value(surface.maximum_fit_condition_number),
            "Maximum Plane Slope": value(surface.maximum_plane_slope_um_per_um, "µm/µm"),
            "Expected Prediction Error": range(focusScan.expected_prediction_error_range_um, "µm"),
          },
        },
        focusLimits: {
          title: "Focus Scan-local Limits",
          items: {
            "Envelope X": range(envelope?.x_um, "µm"),
            "Envelope Y": range(envelope?.y_um, "µm"),
            "Envelope Z": range(envelope?.z_um, "µm"),
            "Maximum Field Elapsed": value(focusScan.maximum_field_elapsed_s, "s"),
            "Maximum Field Z Travel": value(focusScan.maximum_field_z_travel_um, "µm"),
            "Post-move Verification Reserve": value(
              focusScan.post_move_verification_reserve_s,
              "s",
            ),
            "WHITE Search Range": range(white?.search_z_range_um, "µm"),
            "WHITE Search Timeout": value(white?.white_search_timeout_s, "s"),
            "Total Focus Budget": value(white?.total_focus_budget_s, "s"),
            "Approach and R/G Reserve": value(white?.approach_and_rg_reserve_s, "s"),
            "Maximum WHITE/R/G Disagreement": value(white?.maximum_white_led_disagreement_um, "µm"),
          },
        },
      };
      if (run.binding) {
        sections.focusBinding = {
          title: "Focus Binding — Read-only Identity",
          items: {
            Reference: `${run.binding.reference_id} (${run.binding.reference_status})`,
            "Camera-stage Mapping": run.binding.camera_stage_mapping_id,
            "R/G Model": `${run.binding.rg_focus_model_id} (${run.binding.rg_focus_model_status})`,
            "Approach Profile": `${run.binding.approach_profile_id} (${run.binding.approach_status})`,
          },
        };
      }
      return sections;
    },

    /**
     * Format stitching and output related settings.
     *
     * Combines:
     * - stitching_settings
     * - save_resolution
     *
     * into a single display section.
     */
    formatOutputSettings(scanDetails) {
      const output = {};

      if (scanDetails.stitching_settings) {
        Object.entries(scanDetails.stitching_settings).forEach(([key, value]) => {
          const isObject = value !== null && typeof value === "object" && !Array.isArray(value);

          if (isObject) {
            Object.assign(output, flattenGroup(value, formatKey(key)));
          } else {
            output[`Stitching ${formatKey(key)}`] = formatInfoValue(value);
          }
        });
      }

      if (scanDetails.save_resolution) {
        output["Save Resolution"] = `${formatInfoValue(scanDetails.save_resolution)} px`;
      }

      if (!Object.keys(output).length) return {};

      return {
        output: {
          title: "Output & Stitching",
          items: output,
        },
      };
    },
  },
};
</script>

<style scoped>
.modal-settings {
  max-width: 650px;
  max-height: 100%;
  display: flex;
  flex-direction: column;
}

.settings-grid {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-right: 4px;
}

.settings-card {
  background: #f8f9fa;
  border: 1px solid #e5e5e5;
  border-radius: 6px;
  padding: 8px 16px;
}

.card-title {
  font-size: 0.9rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #666;
  margin: 0 0 10px;
  border-bottom: 1px solid #e5e5e5;
  padding-bottom: 4px;
}

.settings-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px 16px;
  margin: 0;
}

.settings-item {
  display: flex;
  justify-content: space-between;
  font-size: 0.9rem;
  line-height: 1.4;
}

.settings-item dt {
  color: #555;
}

.settings-item dd {
  margin-left: 8px;
  font-weight: 600;
  color: #111;
  text-align: right;
  white-space: break-spaces;
}
</style>
