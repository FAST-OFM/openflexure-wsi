<template>
  <div class="matrix-container uk-inline">
    <div class="matrix-wrapper">
      <div class="matrix-bracket" :style="bracketSize">(</div>

      <div class="matrix-grid">
        <div v-for="(row, i) in always2DMatrix" :key="i" class="matrix-row">
          <span v-for="(cell, j) in row" :key="j" class="matrix-cell">
            {{ cell }}
          </span>
        </div>
      </div>

      <div class="matrix-bracket" :style="bracketSize">)</div>
    </div>

    <button
      class="copy-button ofm-muted-interactive-text"
      :class="{ success: copied }"
      :title="'Copy as Python list'"
      @click="copyMatrix"
    >
      <span class="material-symbols-outlined">{{ copySymbol }}</span>
    </button>
  </div>
</template>

<script>
export default {
  name: "MatrixDisplay",
  props: {
    matrix: {
      type: Array,
      required: true,
    },
    bracketHeight: {
      type: Number,
      default: 3,
    },
  },
  data() {
    return {
      copied: false,
    };
  },
  computed: {
    always2DMatrix() {
      if (!this.matrix || this.matrix.length === 0) return [];
      if (!Array.isArray(this.matrix[0])) {
        return [this.matrix];
      }
      return this.matrix;
    },
    copySymbol() {
      return this.copied ? "check" : "content_copy";
    },
    bracketSize() {
      return { fontSize: `${this.bracketHeight}rem`, lineHeight: "1" };
    },
  },
  methods: {
    copyMatrix() {
      const pythonFormat = JSON.stringify(this.matrix)
        .replaceAll("],", "], ")
        .replaceAll(",", ", ");
      this.copyText(pythonFormat);
    },
    /**
     * Cannot use modern text copy as we don't have https.
     *
     * This uses the deprecated execCommand as navigator.clipboard.writeText() is not
     * available.
     */
    copyText(text) {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      try {
        document.execCommand("copy");
        this.copied = true;
        setTimeout(() => (this.copied = false), 1000);
      } catch (err) {
        console.error("Copy failed:", err);
      } finally {
        document.body.removeChild(textarea);
      }
    },
  },
};
</script>

<style scoped lang="less">
@import url("../../assets/less/variables.less");

.matrix-container {
  display: inline-block;
  padding: 8px 12px;
  border-radius: 6px;
  position: relative;
}

.matrix-wrapper {
  display: flex;
  align-items: center;
}

.matrix-bracket {
  display: flex;
  align-items: center;
  padding: 0 0.25rem;
}

.matrix-grid {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
}

.matrix-row {
  display: flex;
}

.matrix-cell {
  padding: 2px 6px;
  min-width: 24px;
  text-align: center;
}

.copy-button {
  position: absolute;
  top: -5px;
  right: -20px;
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 4px;
  margin-left: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.2s ease;
}

.copy-button.success {
  color: #4caf50;

  &:hover {
    color: #4caf50;
  }
}
</style>
