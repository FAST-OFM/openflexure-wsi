<template>
  <mini-menu v-slot="{ close }">
    <p>
      <icon-button
        icon="refresh"
        :show-label="true"
        label="Reset to default"
        :disabled="!canReset"
        @click="
          $emit('reset-default');
          close();
        "
      />
    </p>
    <p>
      <icon-button
        icon="sync_alt"
        :show-label="true"
        label="Re-sync value"
        @click="
          $emit('request-update');
          close();
        "
      />
    </p>
  </mini-menu>
</template>
<script>
import iconButton from "@/components/genericComponents/iconButton.vue";
import MiniMenu from "@/components/genericComponents/miniMenu.vue";

export default {
  name: "PropertyControlMenu",

  components: {
    iconButton,
    MiniMenu,
  },

  props: {
    canReset: {
      type: Boolean,
      default: null,
    },
  },
  emits: ["request-update", "reset-default"],
};
</script>
