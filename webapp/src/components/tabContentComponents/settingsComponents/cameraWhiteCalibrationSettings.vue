<template>
  <div class="uk-width-large">
    <h3>WHITE calibration</h3>
    <compact-help
      class="uk-margin-small-bottom"
      label="Before calibration"
      text="Use an evenly illuminated empty field. These actions can change exposure, colour gains and WHITE flat-field correction."
    />
    <camera-calibration-settings :camera-uri="cameraUri" />
  </div>
</template>

<script>
import CameraCalibrationSettings from "./cameraSettingsComponents/cameraCalibrationSettings.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";

export default {
  name: "CameraWhiteCalibrationSettings",
  components: { CameraCalibrationSettings, CompactHelp },
  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    cameraUri() {
      return `${this.baseUri}/camera/`;
    },
  },
};
</script>
