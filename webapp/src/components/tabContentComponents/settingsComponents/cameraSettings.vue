<template>
  <div id="cameraSettings" class="camera-settings">
    <div class="uk-grid uk-grid-divider uk-child-width-expand" uk-grid>
      <div class="uk-width-large">
        <h3>Image</h3>
        <div class="uk-margin-small-bottom">
          <server-specified-property-control
            v-for="(setting, index) in manualCameraSettings"
            :key="'cam_setting' + index"
            :property-data="setting"
          />
        </div>
      </div>

      <div id="mini-stream">
        <miniStreamDisplay :stream-id="setStreamId" />
      </div>
    </div>
  </div>
</template>

<script>
import miniStreamDisplay from "../../genericComponents/miniStreamDisplay.vue";
import ServerSpecifiedPropertyControl from "../../labThingsComponents/serverSpecifiedPropertyControl.vue";

// Export main app
export default {
  name: "CameraSettings",

  components: {
    miniStreamDisplay,
    ServerSpecifiedPropertyControl,
  },

  data() {
    return {
      manualCameraSettings: [],
      // This adds the parent name as value for prop streamId
      setStreamId: this.$options.name,
    };
  },

  async created() {
    this.manualCameraSettings = await this.readThingProperty("camera", "manual_camera_settings");
  },
};
</script>

<style lang="less">
#mini-stream {
  min-width: 300px;
  max-width: 600px;
  text-align: center;
  margin-left: auto;
  margin-right: auto;
  margin-top: 50px;
}

.camera-settings #mini-stream .stream-display {
  height: auto;
}
</style>
