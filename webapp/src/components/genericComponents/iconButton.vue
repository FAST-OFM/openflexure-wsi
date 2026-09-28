<template>
  <a class="sync-button" :class="{ disabled: disabled }" @click.prevent="$emit('click')">
    <span class="material-symbols-outlined sync-icon">
      {{ icon }}
    </span>
    <span v-if="showLabel" class="icon-label ofm-unstyled-link" :class="{ disabled: disabled }">
      {{ label }}
    </span>
  </a>
</template>

<script>
export default {
  name: "IconButton",

  props: {
    icon: {
      type: String,
      default: "sync_alt",
    },
    showLabel: {
      type: Boolean,
      default: false,
    },
    label: {
      type: String,
      default: "",
    },
    disabled: {
      type: Boolean,
      default: false,
    },
  },

  emits: ["click"],
};
</script>

<style scoped>
.sync-button {
  display: inline-flex;
  flex-grow: 0;
  padding-left: 5px;
  padding-right: 5px;
  vertical-align: middle;
  cursor: pointer;
  user-select: none;
  text-decoration: none !important;
}

.sync-button.disabled {
  cursor: default;
}

.sync-button .material-symbols-outlined.sync-icon {
  color: #888;
  transition: color 0.3s ease;
}

.sync-button.disabled .material-symbols-outlined.sync-icon {
  color: #aaa;
  transition: color 0.3s ease;
}

.sync-button:not(.disabled):hover .material-symbols-outlined.sync-icon {
  color: #c5247f;
  text-decoration: none !important;
}

.icon-label {
  padding-left: 10px;
  padding-top: 3px;
}
</style>
