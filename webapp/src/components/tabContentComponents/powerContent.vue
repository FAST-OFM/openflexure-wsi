<template>
  <!-- Grid managing tab content -->
  <div class="container">
    <h1 id="power-title">Power</h1>
    <div v-if="isRaspberrypi" id="power-msg">
      <p>It's essential to turn off your OpenFlexure Microscope here before unplugging it.</p>
      <p>Unplugging the microscope unexpectedly can damage the SD card or onboard computer.</p>
    </div>
    <div v-else id="power-msg">
      <p>Shutdown the microscope server.</p>
    </div>
    <div class="buttons-container">
      <button
        v-show="shutdownAvailable"
        class="uk-button uk-button-primary uk-width-1-3 shutdown-button"
        @click="systemRequest('shutdown')"
      >
        Shutdown
      </button>
      <button
        v-if="isRaspberrypi"
        class="uk-button uk-button-primary uk-width-1-3 shutdown-button"
        @click="systemRequest('reboot')"
      >
        Restart
      </button>
    </div>
  </div>
</template>

<script>
import axios from "axios";
import { useWotStore } from "@/stores/wot.js";
import { mapActions } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";

export default {
  name: "PowerContent",

  components: {},

  data: function () {
    return {
      isRaspberrypi: undefined,
    };
  },

  computed: {
    shutdownAvailable() {
      return this.thingActionAvailable("system", "shutdown");
    },
  },

  async mounted() {
    this.isRaspberrypi = await this.readThingProperty("system", "is_raspberrypi");
  },

  methods: {
    ...mapActions(useSettingsStore, ["resetState"]),
    ...mapActions(useWotStore, ["deleteAllThingDescriptions"]),
    systemRequest: function (action) {
      let message;
      if (action == "reboot") {
        message = "Restart microscope?";
      } else {
        message = "Shutdown microscope?";
      }
      this.modalConfirm(message).then(
        () => {
          // Post and silence errors
          axios.post(this.thingActionUrl("system", action)).catch(() => {});
          this.resetState();
          this.deleteAllThingDescriptions();
        },
        () => {},
      );
    },
  },
};
</script>

<style lang="less" scoped>
// Custom UIkit CSS modifications
@import url("../../assets/less/variables.less");

.container {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  width: 100%;
  height: 100%;
  padding: 2em;
  box-sizing: border-box;
}

#power-title {
  text-align: center;
}

#power-msg {
  text-align: center;
}

.buttons-container {
  width: 100%;
  text-align: center;
}

.shutdown-button {
  display: inline;
  text-align: center;
  margin: 30px;
}
</style>
