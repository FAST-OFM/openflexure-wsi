<template>
  <stepTemplateWithStream>
    <p>
      <b
        >Once your field of view appears empty, and the illumination looks centred, click Full
        Auto-Calibrate.</b
      >
    </p>

    <template #below-stream>
      <div class="action-button-container">
        <cameraCalibrationSettings
          :show-extra-settings="false"
          :camera-uri="cameraUri"
          @action-started="onActionStart"
          @action-finished="onActionComplete"
        />
      </div>
    </template>
  </stepTemplateWithStream>
</template>

<script>
import stepTemplateWithStream from "../stepTemplateWithStream.vue";
import cameraCalibrationSettings from "../../../tabContentComponents/settingsComponents/cameraSettingsComponents/cameraCalibrationSettings.vue";
import { useSettingsStore } from "@/stores/settings.js";
import { mapState } from "pinia";

export default {
  name: "CameraMainCalibrationStep",

  components: {
    stepTemplateWithStream,
    cameraCalibrationSettings,
  },

  emits: ["prevent-navigation", "awaiting-user"],

  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    cameraUri: function () {
      return `${this.baseUri}/camera/`;
    },
  },

  mounted() {
    this.checkCalibrationState();
  },

  methods: {
    onActionStart() {
      this.$emit("prevent-navigation", true);
    },
    async onActionComplete() {
      await this.checkCalibrationState();
      this.$emit("prevent-navigation", false);
    },
    async checkCalibrationState() {
      const needsCal = await this.readThingProperty("camera", "calibration_required");
      this.$emit("awaiting-user", needsCal);
    },
  },
};
</script>

<style scoped>
.action-button-container {
  padding: 4px;
}
</style>
