<template>
  <div id="streamSettings" class="uk-width-large">
    <div>
      <h4>Web preview</h4>
      <label>
        <input v-model="disableStream" class="uk-checkbox" type="checkbox" />
        Disable live preview
      </label>
    </div>
  </div>
</template>

<script>
import { mapWritableState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";

// Export main app
export default {
  name: "StreamSettings",

  data: function () {
    return {};
  },

  computed: {
    ...mapWritableState(useSettingsStore, ["disableStream"]),
  },
};
</script>

<style lang="less"></style>
