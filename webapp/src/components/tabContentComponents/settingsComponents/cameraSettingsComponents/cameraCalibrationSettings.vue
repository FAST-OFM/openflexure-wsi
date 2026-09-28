<template>
  <div>
    <!--Show calibration actions as specified by the camera.-->
    <div
      v-for="(action, index) in primaryCalibrationActions"
      :key="'primary_cal' + index"
      class="uk-child-width-expand"
    >
      <server-specified-action-button
        :action-data="action"
        @started="$emit('actionStarted')"
        @finished="$emit('actionFinished')"
      />
    </div>
    <div v-if="showExtraSettings">
      <div
        v-for="(action, index) in secondaryCalibrationActions"
        :key="'secondary_cal' + index"
        class="uk-child-width-expand"
      >
        <server-specified-action-button
          :action-data="action"
          @started="$emit('actionStarted')"
          @finished="$emit('actionFinished')"
        />
      </div>
    </div>
  </div>
</template>

<script>
import ServerSpecifiedActionButton from "../../../labThingsComponents/serverSpecifiedActionButton.vue";

// Export main app
export default {
  name: "CameraCalibrationSettings",

  components: {
    ServerSpecifiedActionButton,
  },

  props: {
    showExtraSettings: {
      type: Boolean,
      required: false,
      default: true,
    },
    cameraUri: {
      type: String,
      required: true,
    },
  },

  emits: ["actionStarted", "actionFinished"],

  data() {
    return {
      primaryCalibrationActions: [],
      secondaryCalibrationActions: [],
    };
  },

  computed: {
    actions() {
      return this.thingDescription("camera").actions;
    },
  },

  async created() {
    this.primaryCalibrationActions = await this.readThingProperty(
      "camera",
      "primary_calibration_actions",
    );
    this.secondaryCalibrationActions = await this.readThingProperty(
      "camera",
      "secondary_calibration_actions",
    );
  },
};
</script>

<style lang="less"></style>
