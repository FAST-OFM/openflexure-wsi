<template>
  <div>
    <appSettings />
  </div>
</template>

<script>
import appSettings from "./displaySettingsComponents/appSettings.vue";

// Export main app
export default {
  name: "DisplaySettings",

  components: {
    appSettings,
  },
};
</script>
