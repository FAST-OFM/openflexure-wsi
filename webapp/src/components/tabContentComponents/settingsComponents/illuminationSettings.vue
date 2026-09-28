<template>
  <div class="uk-width-large">
    <h3>Illumination</h3>
    <compact-help
      class="uk-margin-small-bottom"
      label="How switching works"
      text="Only one channel is used at a time. Select the active channel again to switch all lights off."
    />
    <div class="light-controls" role="group" aria-label="Illumination channels" :aria-busy="busy">
      <button
        v-for="channel in channels"
        :key="channel"
        type="button"
        class="uk-button"
        :class="state?.channels[channel] ? 'uk-button-primary' : 'uk-button-default'"
        :aria-pressed="state ? state.channels[channel] : 'false'"
        :disabled="channelDisabled(channel)"
        :data-channel="channel"
        @click="selectChannel(channel)"
      >
        {{ channel.toUpperCase() }}
        <span class="light-state">{{
          state ? (state.channels[channel] ? "On" : "Off") : "Unknown"
        }}</span>
      </button>
    </div>
    <p
      v-if="diagnosticAvailable && diagnosticReason"
      class="uk-text-warning uk-text-small"
      role="status"
    >
      {{ diagnosticReason }} WHITE and OFF remain available.
    </p>
    <p role="status" aria-live="polite">
      {{
        busy
          ? "Switching / verifying…"
          : state
            ? "Controller state: " + state.mode.toUpperCase()
            : "Light status unknown"
      }}
    </p>
    <p v-if="state?.mode === 'mixed'" class="uk-text-warning">
      Multiple outputs are enabled. Select one channel to return to single-channel illumination.
    </p>
    <p v-if="error || statusError || state?.error" class="uk-text-danger" role="alert">
      {{ error || statusError || state.error }}
    </p>
    <section class="diagnostic-preview" aria-live="polite">
      <h4>Illumination preview</h4>
      <compact-help
        v-if="diagnosticAvailable && configuredRoi"
        class="uk-margin-small-bottom"
        label="R/G preview area"
        :text="`Processed JPEG8 ROI ${configuredRoi.join(', ')} px (x, y, width, height). The full WHITE field is unchanged.`"
      />
      <mini-stream-display v-if="state?.mode === 'white'" stream-id="illumination-settings-white" />
      <template v-else-if="['red', 'green'].includes(state?.mode)">
        <img
          v-if="diagnostic?.mode === state.mode && diagnostic.image_href"
          class="diagnostic-image"
          :src="diagnosticImageUrl"
          :alt="state.mode.toUpperCase() + ' diagnostic frame'"
        />
        <p v-if="diagnostic?.mode === state.mode" class="uk-text-small">
          {{
            diagnostic.flat_field_applied
              ? state.mode.toUpperCase() +
                " flat-field applied · profile " +
                (diagnostic.profile_id?.slice(0, 8) || "unavailable")
              : "Uncorrected JPEG8 " +
                state.mode.toUpperCase() +
                " frame — " +
                (diagnostic.correction_reason || "No compatible enabled map")
          }}
        </p>
        <p v-else class="uk-text-small">
          {{ busy ? "Capturing a fresh JPEG8 ROI frame…" : "No diagnostic frame captured." }}
        </p>
        <button
          type="button"
          class="uk-button uk-button-default"
          :disabled="busy || !ready || !diagnosticAvailable || Boolean(diagnosticReason)"
          data-diagnostic-refresh
          @click="captureCurrent"
        >
          Refresh {{ state.mode.toUpperCase() }} frame
        </button>
        <compact-help
          class="uk-margin-small-top"
          label="About this image"
          text="This is a processed JPEG8 ROI with display contrast. It is separate from the WHITE View and Control stream."
        />
      </template>
      <p v-else-if="state?.mode === 'off'" class="uk-text-meta">Illumination is off.</p>
      <p v-else class="uk-text-meta">Select WHITE, RED or GREEN to view it here.</p>
    </section>
    <compact-help
      class="uk-margin-top"
      label="Controller status"
      text="Status is Klipper output readback, not an optical sensor. This page does not change saved brightness. Scans and calibrations lock light switching."
    />
  </div>
</template>

<script>
import axios from "axios";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import MiniStreamDisplay from "@/components/genericComponents/miniStreamDisplay.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";

export default {
  name: "IlluminationSettings",
  components: { CompactHelp, MiniStreamDisplay },
  data: () => ({
    channels: ["white", "red", "green"],
    state: null,
    busy: false,
    error: "",
    statusError: "",
    refreshing: false,
    timer: null,
    generation: 0,
    unmounted: false,
    diagnostic: null,
    measurementParameters: null,
    cameraConfiguration: null,
  }),
  computed: {
    ...mapState(useSettingsStore, ["baseUri", "ready"]),
    diagnosticAvailable() {
      return this.thingActionAvailable("rg_flat_field", "select_diagnostic_mode");
    },
    configuredRoi() {
      if (this.measurementParameters?.measurement_domain !== "processed-jpeg-rgb8") return null;
      const roi = this.measurementParameters?.processing_roi;
      return Array.isArray(roi) && roi.length === 4 && roi.every(Number.isInteger) ? roi : null;
    },
    diagnosticReason() {
      if (!this.diagnosticAvailable) return "";
      if (!this.measurementParameters)
        return "R/G JPEG settings are unavailable; wait for reconnection.";
      if (this.measurementParameters?.measurement_domain !== "processed-jpeg-rgb8")
        return "Save the explicit JPEG8 preset and common ROI in Camera flat-field settings before RED/GREEN capture.";
      const size = this.cameraConfiguration?.geometry?.image_size;
      const roi = this.configuredRoi;
      if (
        this.cameraConfiguration?.measurement_space !== "processed-jpeg-rgb8" ||
        !Array.isArray(size) ||
        size.length !== 2 ||
        !size.every((value) => Number.isInteger(value) && value >= 16)
      )
        return "Current JPEG measurement geometry is unavailable.";
      if (
        !roi ||
        roi[0] < 0 ||
        roi[1] < 0 ||
        roi[2] < 16 ||
        roi[3] < 16 ||
        roi[0] + roi[2] > size[0] ||
        roi[1] + roi[3] > size[1]
      )
        return "Save a valid common RED/GREEN ROI in Camera flat-field settings.";
      return "";
    },
    diagnosticImageUrl() {
      return this.diagnostic?.image_href
        ? this.baseUri +
            this.diagnostic.image_href +
            "?frame=" +
            this.diagnostic.sensor_timestamp_ns
        : "";
    },
  },
  watch: {
    baseUri() {
      this.resetConnection();
    },
    ready() {
      this.resetConnection();
    },
  },
  mounted() {
    this.refreshState();
    this.timer = setInterval(() => {
      if (!this.busy) this.refreshState();
    }, 2000);
  },
  beforeUnmount() {
    this.unmounted = true;
    this.generation++;
    clearInterval(this.timer);
  },
  methods: {
    resetConnection() {
      this.generation++;
      this.state = null;
      this.busy = false;
      this.refreshing = false;
      this.error = "";
      this.statusError = "";
      this.diagnostic = null;
      this.measurementParameters = null;
      this.cameraConfiguration = null;
      this.refreshState();
    },
    async refreshState(force = false) {
      if (this.unmounted || !this.ready || this.refreshing || (this.busy && !force)) return;
      const generation = this.generation;
      this.refreshing = true;
      try {
        const [response, parameters, configuration] = await Promise.all([
          axios.get(this.thingPropertyUrl("illumination", "state"), { timeout: 5000 }),
          this.diagnosticAvailable
            ? axios
                .get(this.thingPropertyUrl("rg_flat_field", "parameters"), { timeout: 5000 })
                .catch(() => null)
            : null,
          this.diagnosticAvailable
            ? axios
                .get(this.thingPropertyUrl("camera", "jpeg_measurement_configuration"), {
                  timeout: 5000,
                })
                .catch(() => null)
            : null,
        ]);
        const state = response.data;
        if (this.unmounted || generation !== this.generation) return;
        this.measurementParameters = parameters?.data || null;
        this.cameraConfiguration = configuration?.data || null;
        const modes = ["white", "red", "green", "off", "mixed"];
        if (
          !state?.available ||
          !modes.includes(state.mode) ||
          !this.channels.every((c) => typeof state.channels?.[c] === "boolean")
        ) {
          this.state = null;
          this.statusError = state?.error || "Cannot read the illumination controller.";
        } else {
          if (this.diagnostic && this.diagnostic.mode !== state.mode) this.diagnostic = null;
          this.state = state;
          this.statusError = "";
        }
      } catch {
        if (!this.unmounted && generation === this.generation) {
          this.state = null;
          this.statusError = "Cannot read the illumination controller.";
        }
      } finally {
        if (generation === this.generation) this.refreshing = false;
      }
    },
    async selectChannel(channel) {
      if (this.channelDisabled(channel) || !this.channels.includes(channel)) return;
      const mode = this.state.channels[channel] ? "off" : channel;
      await this.applyMode(mode);
    },
    channelDisabled(channel) {
      return (
        this.busy ||
        !this.state ||
        !this.ready ||
        (channel !== "white" &&
          !this.state.channels[channel] &&
          this.diagnosticAvailable &&
          Boolean(this.diagnosticReason))
      );
    },
    async captureCurrent() {
      if (!["red", "green"].includes(this.state?.mode)) return;
      await this.applyMode(this.state.mode);
    },
    async applyMode(mode) {
      if (this.busy || !this.state || !this.ready) return;
      if (["red", "green"].includes(mode) && this.diagnosticReason) {
        this.error = this.diagnosticReason;
        return;
      }
      const generation = ++this.generation;
      const origin = this.baseUri;
      const diagnosticAction = this.diagnosticAvailable;
      const thing = diagnosticAction ? "rg_flat_field" : "illumination";
      const action = diagnosticAction ? "select_diagnostic_mode" : "set_mode";
      this.busy = true;
      this.state = null;
      this.refreshing = false;
      this.error = "";
      let output = null;
      try {
        const response = await axios.post(
          this.thingActionUrl(thing, action),
          { mode },
          { timeout: 5000 },
        );
        const href = response?.data?.href;
        if (!href) throw new Error("No action acknowledgement");
        const deadline = Date.now() + 60000;
        while (!this.unmounted && generation === this.generation) {
          const result = await axios.get(href, { baseURL: origin, timeout: 5000 });
          const status = result.data.status;
          if (status === "completed") {
            output = result.data.output || null;
            break;
          }
          if (!["pending", "running"].includes(status) || Date.now() >= deadline)
            throw new Error(result.data.log?.[0]?.message || "Light change was not confirmed");
          await new Promise((resolve) => setTimeout(resolve, 200));
        }
        this.diagnostic =
          diagnosticAction && ["red", "green"].includes(mode) && output?.mode === mode
            ? output
            : null;
      } catch (error) {
        if (!this.unmounted && generation === this.generation)
          this.error = "Switch was not confirmed. " + (error.message || "Check current state.");
      } finally {
        if (!this.unmounted && generation === this.generation) {
          await this.refreshState(true);
          if (!this.unmounted && generation === this.generation) this.busy = false;
        }
      }
    },
  },
};
</script>

<style scoped>
.diagnostic-preview {
  margin-top: 1rem;
}

.diagnostic-image {
  display: block;
  width: 100%;
  max-height: 55vh;
  object-fit: contain;
  background: #000;
}

.white-preview {
  height: min(55vh, 420px);
}

.light-controls {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.light-state {
  display: block;
  font-size: 0.8em;
  line-height: 1.5;
  padding-bottom: 8px;
}
</style>
