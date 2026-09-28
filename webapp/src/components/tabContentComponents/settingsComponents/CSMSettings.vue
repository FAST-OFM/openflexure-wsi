<template>
  <div id="CSMSettings">
    <div class="uk-grid uk-grid-divider uk-child-width-expand" uk-grid>
      <div class="uk-width-large">
        <h3>Camera to Stage Mapping</h3>
        <compact-help
          label="What mapping enables"
          text="Maps camera pixels to physical stage motion for click-to-move and accurate tile spacing. Recalibrate after changing optics or sensor crop."
        />
        <CSMCalibrationSettings />
      </div>
      <div id="mini-stream">
        <miniStreamDisplay :stream-id="setStreamId" />
      </div>
    </div>
  </div>
</template>

<script>
import CSMCalibrationSettings from "./CSMSettingsComponents/CSMCalibrationSettings.vue";
import miniStreamDisplay from "../../genericComponents/miniStreamDisplay.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";

// Export main app
export default {
  name: "CSMSettings",

  components: {
    CSMCalibrationSettings,
    miniStreamDisplay,
    CompactHelp,
  },

  data() {
    return {
      // This adds the parent name as value for prop streamId
      setStreamId: this.$options.name,
    };
  },
};
</script>

<style lang="less">
.center-spinner {
  margin-left: auto;
  margin-right: auto;
}

#mini-stream {
  min-width: 300px;
  max-width: 600px;
  text-align: center;
  margin-left: auto;
  margin-right: auto;
  margin-top: 50px;
}
</style>
