<template>
  <div class="uk-width-large">
    <template v-if="boundedStage">
      <p><b>Manual move step</b></p>
      <compact-help
        label="Where these steps apply"
        text="Controls the X/Y arrows and Z buttons in Control. Holding a button repeats only after controller completion and position verification."
      />
      <p v-if="loadError" class="uk-text-danger" role="alert">{{ loadError }}</p>
      <p v-else-if="!hardware">Reading the stage motion profile…</p>
      <form v-else @submit.prevent="saveSteps">
        <div class="uk-grid-small uk-child-width-1-3" uk-grid>
          <div v-for="axis in axes" :key="axis">
            <label class="uk-form-label" :for="'manual-step-' + axis">
              {{ axis.toUpperCase() }} step (µm)
            </label>
            <input
              :id="'manual-step-' + axis"
              v-model.number="draftUm[axis]"
              class="uk-input uk-form-small"
              type="number"
              required
              :min="1000 / hardware.axes[axis].units_per_mm"
              :step="1000 / hardware.axes[axis].units_per_mm"
              :max="
                hardware.axes[axis].single_move_limit_enabled === false
                  ? undefined
                  : hardware.axes[axis].max_move_mm * 1000
              "
              :disabled="!hardware.axes[axis].enabled"
              @input="
                savedMessage = '';
                formError = '';
              "
            />
            <label v-if="axis !== 'z'" class="uk-margin-small-right">
              <input v-model="navigationInvert[axis]" class="uk-checkbox" type="checkbox" />
              Invert {{ axis }}
            </label>
          </div>
        </div>
        <p v-if="formError" class="uk-text-danger" role="alert">{{ formError }}</p>
        <div class="uk-margin-small-top">
          <button type="submit" class="uk-button uk-button-primary uk-button-small">
            Apply steps
          </button>
          <button
            type="button"
            class="uk-button uk-button-default uk-button-small uk-margin-small-left"
            @click="restoreDefaults"
          >
            Restore profile defaults
          </button>
        </div>
        <p v-if="savedMessage" class="uk-text-meta" role="status">{{ savedMessage }}</p>
      </form>
      <template v-if="hardware">
        <p><b>Shared motion profile</b></p>
        <compact-help
          label="Timing"
          :text="`Manual settle ${hardware.manual_settle_ms} ms; hold repeat ${hardware.ui_repeat_delay_ms} ms. Scans and calibrations use the automatic settle values below.`"
        />
        <table class="uk-table uk-table-small uk-table-divider">
          <thead>
            <tr>
              <th>Axis</th>
              <th>Speed (mm/s)</th>
              <th>Auto settle (ms)</th>
              <th>Max step (microns)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="axis in axes" :key="axis">
              <td>{{ axis.toUpperCase() }}</td>
              <td>{{ hardware.axes[axis].speed_mm_s }}</td>
              <td>{{ hardware.axes[axis].settle_ms }}</td>
              <td>
                {{
                  hardware.axes[axis].single_move_limit_enabled === false
                    ? "Off"
                    : hardware.axes[axis].max_move_mm * 1000
                }}
              </td>
            </tr>
          </tbody>
        </table>
        <compact-help
          label="Limits and keyboard control"
          text="Travel and single-move limits are under Stage → Limits. Speed and settling come from the hardware profile. Global keyboard and mouse-wheel movement are disabled; use Control."
        />
      </template>
    </template>
    <template v-else>
      <p><b>Single Move Step Size</b></p>
      <p>
        This sets the size (and direction) of movements made using the navigation buttons in the
        control tab or using the keyboard (arrow keys, page up/down).
      </p>
      <p>These settings do not affect the operation of other actions your microscope performs.</p>
      <div class="uk-grid-small uk-child-width-1-3" uk-grid>
        <div>
          <label class="uk-form-label" for="form-stacked-text">x</label>
          <div class="uk-form-controls">
            <input v-model="navigationStepSize.x" class="uk-input uk-form-small" type="number" />
          </div>
          <label class="uk-margin-small-right">
            <input v-model="navigationInvert.x" class="uk-checkbox" type="checkbox" />
            Invert x
          </label>
        </div>
        <div>
          <label class="uk-form-label" for="form-stacked-text">y</label>
          <div class="uk-form-controls">
            <input v-model="navigationStepSize.y" class="uk-input uk-form-small" type="number" />
          </div>
          <label class="uk-margin-small-right">
            <input v-model="navigationInvert.y" class="uk-checkbox" type="checkbox" />
            Invert y
          </label>
        </div>
        <div>
          <label class="uk-form-label" for="form-stacked-text">z</label>
          <div class="uk-form-controls">
            <input v-model="navigationStepSize.z" class="uk-input uk-form-small" type="number" />
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script>
import { eventBus } from "@/eventBus.js";
import { storeToRefs } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import { manualStageStep } from "@/js_utils/stageControlPreferences.js";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";

export default {
  name: "StageControlSettings",
  components: { CompactHelp },

  setup() {
    const store = useSettingsStore();
    const { navigationStepSize, navigationInvert, stageNavigationStepsMm, baseUri } =
      storeToRefs(store);
    return { navigationStepSize, navigationInvert, stageNavigationStepsMm, baseUri };
  },
  data: () => ({
    axes: ["x", "y", "z"],
    hardware: null,
    draftUm: {},
    loadError: "",
    formError: "",
    savedMessage: "",
  }),
  computed: {
    boundedStage() {
      return Boolean(this.thingDescription("stage")?.actions?.enable_motors);
    },
  },
  watch: {
    baseUri() {
      this.loadProfile();
    },
    boundedStage() {
      this.loadProfile();
    },
  },
  mounted() {
    this.loadProfile();
    eventBus.on("stageMotionLimitsChanged", this.loadProfile);
    eventBus.on("stageMotionDynamicsChanged", this.loadProfile);
  },
  beforeUnmount() {
    eventBus.off("stageMotionLimitsChanged", this.loadProfile);
    eventBus.off("stageMotionDynamicsChanged", this.loadProfile);
  },
  methods: {
    async loadProfile() {
      this.hardware = null;
      this.loadError = "";
      this.formError = "";
      this.savedMessage = "";
      if (!this.boundedStage) return;
      const origin = this.baseUri;
      try {
        const hardware = await this.readThingProperty("stage", "hardware_settings", true);
        if (origin !== this.baseUri) return;
        if (!this.axes.every((axis) => hardware?.axes?.[axis]))
          throw new Error("Incomplete profile");
        this.hardware = hardware;
        this.loadDraft();
      } catch {
        if (origin === this.baseUri)
          this.loadError =
            "Cannot read the stage motion profile. No step settings have been changed.";
      }
    },
    loadDraft() {
      const saved = this.stageNavigationStepsMm[this.baseUri] ?? {};
      this.draftUm = Object.fromEntries(
        this.axes.map((axis) => [
          axis,
          (saved[axis] ?? this.hardware.axes[axis].ui_step_mm) * 1000,
        ]),
      );
    },
    saveSteps() {
      if (!this.hardware) return;
      const steps = Object.fromEntries(
        this.axes.map((axis) => [axis, Number(this.draftUm[axis]) / 1000]),
      );
      const invalid = this.axes.filter(
        (axis) =>
          this.hardware.axes[axis].enabled &&
          !manualStageStep(this.hardware.axes[axis], steps[axis]).valid,
      );
      if (invalid.length) {
        this.formError = `Check ${invalid.map((axis) => axis.toUpperCase()).join(", ")}: use positive steps within the shown limits and supported resolution.`;
        this.savedMessage = "";
        return;
      }
      this.stageNavigationStepsMm[this.baseUri] = steps;
      this.formError = "";
      this.savedMessage = "Applied to Control. Saved in this browser for this microscope.";
    },
    restoreDefaults() {
      delete this.stageNavigationStepsMm[this.baseUri];
      this.loadDraft();
      this.formError = "";
      this.savedMessage = "Profile defaults applied to Control.";
    },
  },
};
</script>
