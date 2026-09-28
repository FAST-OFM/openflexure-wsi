<template>
  <div class="uk-flex uk-flex-column uk-flex-center uk-height-1-1">
    <div v-if="waiting" class="uk-align-center uk-text-center">
      <div uk-spinner="ratio: 3"></div>
      <p>Loading...</p>
    </div>
    <div v-if="error" id="error-container" class="uk-align-center">
      <span class="material-symbols-outlined uk-align-center error-icon">error_outline</span>
      <p>{{ error }}</p>
    </div>
  </div>
</template>

<script>
import { mapWritableState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";

// Export main app
export default {
  name: "LoadingContent",

  computed: {
    ...mapWritableState(useSettingsStore, ["error", "waiting"]),
  },
};
</script>

<style scoped lang="less">
#error-container {
  text-align: center;
}

.error-icon {
  font-size: 120px;
}
</style>
