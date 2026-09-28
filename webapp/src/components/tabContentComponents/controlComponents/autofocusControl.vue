<template>
  <div>
    <template v-if="stageMode === 'physical' && manualMethod === 'openflexure'">
      <div
        class="autofocus-range uk-margin-small-bottom"
        :title="`Autofocus Z search range: total sweep ±${rangeUm / 2} µm around current Z. Separate from scan settings.`"
      >
        <label class="uk-form-label" for="manual-autofocus-range">Z (µm)</label>
        <input
          id="manual-autofocus-range"
          v-model.number="rangeUm"
          class="uk-input uk-form-small"
          aria-label="Autofocus Z search range (µm)"
          type="number"
          min="0"
          step="5"
          :disabled="isAutofocusing || !hardware"
        />
      </div>
      <compact-help
        v-if="rangeError"
        class="uk-margin-small-bottom"
        label="Check Z range"
        :text="rangeError"
        tone="warning"
      />
    </template>
    <div class="uk-margin-small-bottom">
      <action-button
        :thing="actionThing"
        :action="actionName"
        :submit-data="submitData"
        :is-disabled="!isAutofocusing && !canSubmit"
        :submit-label="'Autofocus'"
        :button-primary="true"
        :submit-on-event="'globalFastAutofocusEvent'"
        @task-started="onAutofocus"
        @finished="afterAutofocus"
        @error="modalError"
      />
    </div>
    <div class="autofocus-method uk-margin-small-bottom">
      <label class="uk-form-label" for="manual-autofocus-method">Focus method</label>
      <select
        id="manual-autofocus-method"
        v-model="manualMethod"
        class="uk-select uk-form-small"
        aria-label="Manual autofocus method"
        :disabled="isAutofocusing || !ready"
      >
        <option value="openflexure">Standard (WHITE)</option>
        <option value="led">R/G + fallback</option>
        <option value="simultaneous_rg">Simultaneous R+G + fallback</option>
      </select>
    </div>
    <compact-help v-if="methodError" :label="methodErrorLabel" :text="methodError" tone="warning" />
  </div>
</template>
<script>
import ActionButton from "../../labThingsComponents/actionButton.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";
import { eventBus } from "../../../eventBus.js";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import { manualStageStep } from "@/js_utils/stageControlPreferences.js";

export default {
  name: "AutofocusControl",

  components: {
    ActionButton,
    CompactHelp,
  },

  data() {
    return {
      isAutofocusing: false,
      hardware: null,
      requestId: 0,
      rgReadiness: null,
      rgRequestId: 0,
      simultaneousReadiness: null,
      simultaneousRequestId: 0,
    };
  },

  computed: {
    ...mapState(useSettingsStore, [
      "baseUri",
      "ready",
      "manualAutofocusRangesUm",
      "manualAutofocusMethods",
    ]),
    manualMethod: {
      get() {
        const saved = this.manualAutofocusMethods[this.baseUri];
        return ["openflexure", "led", "simultaneous_rg"].includes(saved) ? saved : "openflexure";
      },
      set(value) {
        if (["openflexure", "led", "simultaneous_rg"].includes(value)) {
          useSettingsStore().manualAutofocusMethods[this.baseUri] = value;
        }
      },
    },
    stageMode() {
      const stage = this.thingDescription("stage");
      if (!stage?.properties) return "loading";
      return stage.properties.hardware_settings ? "physical" : "native";
    },
    limitUm() {
      return this.hardware?.axes?.z?.single_move_limit_enabled === false
        ? undefined
        : this.hardware?.axes?.z?.max_move_mm * 1000;
    },
    rangeUm: {
      get() {
        const saved = this.manualAutofocusRangesUm[this.baseUri];
        if (saved !== undefined) return saved;
        return Number.isFinite(this.limitUm) ? Math.min(50, this.limitUm) : 50;
      },
      set(value) {
        useSettingsStore().manualAutofocusRangesUm[this.baseUri] = value;
      },
    },
    sweep() {
      return manualStageStep(this.hardware?.axes?.z, this.rangeUm / 1000);
    },
    rangeError() {
      if (!this.hardware) return "Z settings unavailable — reconnect or reload the page.";
      if (!this.hardware.allow_motion || !this.hardware.axes?.z?.enabled) {
        return "Z motion is disabled in the stage settings.";
      }
      if (!this.sweep.valid) {
        return Number.isFinite(this.limitUm)
          ? `Enter a positive range up to ${this.limitUm} µm, matching the stage resolution.`
          : "Enter a positive range matching the stage resolution. Controller bounds and timeouts still apply.";
      }
      return "";
    },
    canSubmit() {
      if (!this.ready || this.stageMode === "loading") return false;
      if (this.manualMethod === "led") return this.rgReadiness?.autofocus_ready === true;
      if (this.manualMethod === "simultaneous_rg") {
        return this.simultaneousReadiness?.autofocus_ready === true;
      }
      return this.stageMode === "native" || !this.rangeError;
    },
    actionThing() {
      if (this.manualMethod === "simultaneous_rg") return "rg_simultaneous";
      return this.manualMethod === "led" ? "rg_focus" : "autofocus";
    },
    actionName() {
      if (this.manualMethod === "simultaneous_rg") return "autofocus";
      return this.manualMethod === "led" ? "autofocus_with_white_fallback" : "fast_autofocus";
    },
    submitData() {
      if (["led", "simultaneous_rg"].includes(this.manualMethod)) {
        return { prepared: true, white_dz: null };
      }
      // Stock stages retain their native units. A missing physical profile must
      // never fall back to the stock 2000-unit (2 mm on our stage) sweep.
      return { dz: this.stageMode === "native" ? 2000 : this.sweep.units, start: "centre" };
    },
    methodError() {
      if (this.manualMethod === "simultaneous_rg") {
        if (!this.ready) return "";
        if (this.simultaneousReadiness === null) {
          return "Checking simultaneous R/G autofocus readiness...";
        }
        if (this.simultaneousReadiness.autofocus_ready) return "";
        return (
          this.simultaneousReadiness.reason ||
          "Open Settings → Camera → Autofocus to see what needs attention."
        );
      }
      if (this.manualMethod !== "led" || !this.ready) return "";
      if (this.rgReadiness === null) return "Checking R/G autofocus readiness...";
      if (this.rgReadiness.autofocus_ready) return "";
      const codes = new Set((this.rgReadiness.mismatches || []).map(({ code }) => code));
      if (codes.has("control_contract_mismatch")) {
        return "The saved R/G calibration is valid, but the active focus settings do not match it.";
      }
      if (codes.has("flat_field_invalid") || codes.has("flat_field_profile_mismatch")) {
        return "The RED/GREEN flat-field calibration is missing or no longer matches the camera.";
      }
      if (codes.has("focus_profile_missing")) return "The R/G focus curve has not been calibrated.";
      if (codes.has("focus_profile_candidate")) {
        return "The latest R/G focus curve has not passed validation.";
      }
      return "Open Settings → Camera → Autofocus to see what needs attention.";
    },
    methodErrorLabel() {
      if (this.manualMethod === "simultaneous_rg") {
        return this.simultaneousReadiness === null
          ? "Checking simultaneous R/G focus"
          : "Simultaneous R/G focus unavailable";
      }
      if (this.rgReadiness === null) return "Checking R/G focus";
      const codes = new Set((this.rgReadiness.mismatches || []).map(({ code }) => code));
      if (codes.has("control_contract_mismatch")) return "R/G settings changed";
      if (codes.has("flat_field_invalid") || codes.has("flat_field_profile_mismatch")) {
        return "R/G flat-field unavailable";
      }
      if (codes.has("focus_profile_missing")) return "R/G curve missing";
      if (codes.has("focus_profile_candidate")) return "R/G curve not validated";
      return "R/G focus unavailable";
    },
  },

  watch: {
    stageMode: { immediate: true, handler: "refreshHardware" },
    manualMethod: { immediate: true, handler: "refreshFocusReadiness" },
    baseUri: "refreshConnectionState",
    ready: "refreshConnectionState",
  },

  mounted() {
    eventBus.on("stageMotionLimitsChanged", this.refreshHardware);
  },
  beforeUnmount() {
    eventBus.off("stageMotionLimitsChanged", this.refreshHardware);
    this.requestId += 1;
    this.rgRequestId += 1;
    this.simultaneousRequestId += 1;
  },

  methods: {
    refreshConnectionState() {
      this.refreshHardware();
      this.refreshFocusReadiness();
    },
    refreshFocusReadiness() {
      this.refreshRgReadiness();
      this.refreshSimultaneousReadiness();
    },
    async refreshHardware() {
      const requestId = ++this.requestId;
      this.hardware = null;
      if (!this.ready || this.stageMode !== "physical") return;
      const hardware = await this.readThingProperty("stage", "hardware_settings", true);
      if (requestId === this.requestId) this.hardware = hardware || null;
    },
    async refreshRgReadiness() {
      const requestId = ++this.rgRequestId;
      this.rgReadiness = null;
      if (!this.ready || this.manualMethod !== "led") return;
      const description = this.thingDescription("rg_focus");
      if (!description?.actions?.autofocus_with_white_fallback) {
        if (requestId === this.rgRequestId) this.rgReadiness = { autofocus_ready: false };
        return;
      }
      try {
        const readiness = await this.readThingProperty("rg_focus", "readiness", true);
        if (requestId === this.rgRequestId) {
          this.rgReadiness = readiness || { autofocus_ready: false };
        }
      } catch {
        if (requestId === this.rgRequestId) this.rgReadiness = { autofocus_ready: false };
      }
    },
    async refreshSimultaneousReadiness() {
      const requestId = ++this.simultaneousRequestId;
      this.simultaneousReadiness = null;
      if (!this.ready || this.manualMethod !== "simultaneous_rg") return;
      const description = this.thingDescription("rg_simultaneous");
      if (!description?.actions?.autofocus) {
        if (requestId === this.simultaneousRequestId) {
          this.simultaneousReadiness = {
            autofocus_ready: false,
            reason: "Simultaneous R/G autofocus component is unavailable.",
          };
        }
        return;
      }
      try {
        const readiness = await this.readThingProperty(
          "rg_simultaneous",
          "autofocus_readiness",
          true,
        );
        if (requestId === this.simultaneousRequestId) {
          this.simultaneousReadiness = readiness || {
            autofocus_ready: false,
            reason: "Simultaneous R/G readiness is unavailable.",
          };
        }
      } catch {
        if (requestId === this.simultaneousRequestId) {
          this.simultaneousReadiness = {
            autofocus_ready: false,
            reason: "Simultaneous R/G readiness is unavailable.",
          };
        }
      }
    },
    onAutofocus() {
      this.isAutofocusing = true;
    },
    afterAutofocus() {
      this.isAutofocusing = false;
      this.refreshFocusReadiness();
      eventBus.emit("globalUpdatePositionEvent");
    },
  },
};
</script>

<style scoped>
.autofocus-range {
  display: flex;
  align-items: center;
  gap: 8px;
}

.autofocus-method {
  display: block;
}

.autofocus-method label {
  display: block;
  margin-bottom: 4px;
}

.autofocus-method select {
  display: block;
  width: 100%;
  min-width: 0;
  padding-right: 32px;
}

.autofocus-range label {
  white-space: nowrap;
}

.autofocus-range input {
  flex: 1;
  min-width: 0;
}
</style>
