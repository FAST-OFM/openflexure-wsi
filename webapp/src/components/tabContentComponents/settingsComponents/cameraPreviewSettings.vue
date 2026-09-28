<template>
  <div class="uk-width-large camera-preview-settings">
    <h3>Preview</h3>
    <stream-settings />
    <section v-if="ready" class="uk-margin-top">
      <h4>Camera stream</h4>
      <dl v-if="streamingMode" class="preview-summary">
        <div>
          <dt>Mode</dt>
          <dd>{{ streamingMode }}</dd>
        </div>
        <div v-if="previewResolution">
          <dt>Source</dt>
          <dd>{{ previewResolution[0] }} × {{ previewResolution[1] }} px</dd>
        </div>
      </dl>
      <property-control
        v-if="streamingModes"
        thing-name="camera"
        property-name="preview_jpeg_quality"
        label="Preview JPEG quality (1–100)"
      />
      <p v-if="streamingModes" class="uk-text-meta">
        Applies when streaming next starts. Does not change scan images or autofocus.
      </p>
      <div class="uk-margin-small-top">
        <property-control
          thing-name="camera"
          property-name="settling_time"
          label="Camera settle time (s)"
        />
      </div>
      <details class="uk-margin-top">
        <summary>Analysis preview</summary>
        <div class="uk-margin-small-top">
          <property-control
            thing-name="camera"
            property-name="downsampled_array_factor"
            label="Analysis downsample factor"
          />
        </div>
        <p class="uk-text-meta">
          Used by camera-stage mapping and other downsampled analysis. It does not crop the sensor.
        </p>
      </details>
    </section>
  </div>
</template>

<script>
import PropertyControl from "@/components/labThingsComponents/propertyControl.vue";
import StreamSettings from "./displaySettingsComponents/streamSettings.vue";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";

export default {
  name: "CameraPreviewSettings",
  components: { PropertyControl, StreamSettings },
  data: () => ({ streamingMode: "", streamingModes: null }),
  computed: {
    ...mapState(useSettingsStore, ["ready", "baseUri"]),
    previewResolution() {
      const mode = this.streamingModes?.[this.streamingMode];
      if (!mode) return null;
      return mode.use_lores_as_preview ? mode.lores_resolution : mode.main_resolution;
    },
  },
  watch: {
    ready: { immediate: true, handler: "load" },
    baseUri: "load",
  },
  methods: {
    async load() {
      this.streamingMode = "";
      this.streamingModes = null;
      if (!this.ready) return;
      const origin = this.baseUri;
      try {
        const [mode, modes] = await Promise.all([
          this.readThingProperty("camera", "streaming_mode", true),
          this.readThingProperty("camera", "streaming_modes", true),
        ]);
        if (origin !== this.baseUri) return;
        this.streamingMode = mode;
        this.streamingModes = modes;
      } catch {
        // Individual property controls still report their own connection errors.
      }
    },
  },
};
</script>

<style scoped>
.preview-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.5rem;
}

.preview-summary div {
  padding: 0.5rem;
  border: 1px solid rgba(127, 127, 127, 0.25);
}

.preview-summary dt {
  color: #777;
  font-size: 0.75rem;
  text-transform: uppercase;
}

.preview-summary dd {
  margin: 0;
}
</style>
