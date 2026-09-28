<template>
  <div id="appSettings" class="uk-width-large">
    <h3>Appearance</h3>
    <p>
      <label>
        Theme
        <select v-model="appTheme" class="uk-select">
          <option value="light">Light</option>
          <option value="dark">Dark</option>
          <option value="system">System</option>
        </select>
      </label>
    </p>
    <p>
      <button class="uk-button uk-button-default" @click="toggleFullscreen">
        Toggle Fullscreen
      </button>
    </p>
  </div>
</template>
<script>
import { useSettingsStore } from "@/stores/settings.js";
import { mapWritableState } from "pinia";

// Export main app
export default {
  name: "AppSettings",

  data: function () {
    return {};
  },

  computed: {
    ...mapWritableState(useSettingsStore, ["appTheme"]),
  },

  methods: {
    async toggleFullscreen() {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    },
  },
};
</script>

<style lang="less"></style>
