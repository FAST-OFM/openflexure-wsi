<template>
  <div class="host-input">
    <div v-if="ready">
      <div>
        <div class="uk-margin-small-bottom">
          <b>Microscope Hostname:</b>
          <br />
          {{ microscopeHostname }}
        </div>
        <div class="uk-margin-small-bottom">
          <b>API Origin:</b>
          <br />
          {{ baseUri }}
        </div>
        <action-button
          v-if="illuminationType"
          thing="illumination"
          action="flash"
          submit-label="Flash Illumination"
          :can-terminate="false"
          :submit-data="{ dt: 0.25 }"
        />
      </div>

      <template v-if="isRaspberrypi">
        <hr />
        <div>
          <b>Microscope ID:</b> <br />
          {{ microscopeId }}<br />
          <b>Hardware ID:</b> <br />
          {{ hardwareId }}
        </div>
      </template>

      <hr />

      <div>
        <b>Server Version:</b> <br />
        {{ version }}<br />
        ({{ version_source }})
      </div>

      <hr />

      <div class="uk-margin-small-bottom">
        <b>Camera:</b>
        <br />
        <div>
          {{ cameraType }}
        </div>
      </div>
      <div v-if="stageType">
        <b>Stage:</b>
        <br />
        <div>
          {{ stageType }}
        </div>
      </div>
      <div v-if="illuminationType">
        <b>Illumination:</b>
        <br />
        <div>
          {{ illuminationType }}
        </div>
      </div>

      <hr />
    </div>
    <div v-else-if="waiting">Loading...</div>
    <div v-else-if="error"><b>Error:</b> {{ error }}</div>
    <div v-else>No active connection</div>
  </div>
</template>

<script>
import ActionButton from "../../labThingsComponents/actionButton.vue";
import { mapState, mapWritableState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import { useWotStore } from "@/stores/wot.js";

export default {
  name: "StatusPane",
  components: {
    ActionButton,
  },

  data: function () {
    return {
      version: undefined,
      version_source: undefined,
      isRaspberrypi: undefined,
      microscopeId: undefined,
      hardwareId: undefined,
    };
  },

  computed: {
    ...mapState(useSettingsStore, ["baseUri", "microscopeHostname", "ready", "waiting"]),
    ...mapState(useWotStore, ["thingDescriptions"]),
    ...mapWritableState(useSettingsStore, ["error"]),

    cameraType() {
      // No need to check as the microscope won't start up without a camera defined
      return this.thingDescriptions["camera"]?.title;
    },
    stageType() {
      return this.thingAvailable("stage") ? this.thingDescriptions["stage"]?.title : undefined;
    },
    illuminationType() {
      return this.thingAvailable("illumination")
        ? this.thingDescriptions["illumination"]?.title
        : undefined;
    },
  },

  async mounted() {
    let version_data = await this.readThingProperty("system", "version_data");
    this.version = version_data.version;
    this.version_source = this.truncate(version_data.version_source);
    this.isRaspberrypi = await this.readThingProperty("system", "is_raspberrypi");
    this.microscopeId = await this.readThingProperty("system", "microscope_id");
    this.hardwareId = await this.readThingProperty("system", "hardware_id");
  },

  methods: {
    truncate(string, max_length = 15) {
      if (!(typeof string === "string" || string instanceof String)) {
        return string;
      }
      if (string.length <= max_length) {
        return string;
      }
      return string.slice(0, max_length - 3) + "...";
    },
  },
};
</script>

<!-- Add "scoped" attribute to limit CSS to this component only -->
<style scoped lang="less"></style>
