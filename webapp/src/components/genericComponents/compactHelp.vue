<template>
  <details class="compact-help" :class="`compact-help-${tone}`">
    <summary :title="text">
      <span class="material-symbols-outlined compact-help-icon" aria-hidden="true">
        {{ icon }}
      </span>
      <span>{{ label }}</span>
    </summary>
    <p class="uk-text-small uk-margin-small-top uk-margin-remove-bottom">{{ text }}</p>
  </details>
</template>

<script>
export default {
  name: "CompactHelp",
  props: {
    label: { type: String, required: true },
    text: { type: String, required: true },
    tone: {
      type: String,
      default: "muted",
      validator: (value) => ["muted", "warning", "danger"].includes(value),
    },
  },
  computed: {
    icon() {
      return this.tone === "danger" ? "error" : this.tone === "warning" ? "warning" : "info";
    },
  },
};
</script>

<style scoped>
.compact-help {
  font-size: 0.875rem;
}

.compact-help summary {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  cursor: help;
  list-style: none;
}

.compact-help summary::-webkit-details-marker {
  display: none;
}

.compact-help summary:focus-visible {
  outline: 2px solid currentcolor;
  outline-offset: 3px;
}

.compact-help-icon {
  font-size: 1.1rem;
}

.compact-help-muted {
  color: var(--ofm-muted-color, #666);
}

.compact-help-warning {
  color: #a86611;
}

.compact-help-danger {
  color: #b3261e;
}
</style>
