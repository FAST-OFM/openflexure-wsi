<template>
  <div v-for="(element, index) in elements" :key="index" class="uk-margin">
    <!-- eslint-disable vue/no-v-html -->
    <component :is="'h' + element.level" v-if="element.element_type === 'header_block'">
      <span v-html="element.text"></span>
    </component>
    <p v-if="element.element_type === 'text_block'" v-html="element.text"></p>
    <compact-help
      v-else-if="element.element_type === 'help_block'"
      :label="element.label"
      :text="element.text"
    />
    <ul v-if="element.element_type === 'bullet_block'">
      <li
        v-for="(bulletText, bulletIndex) in element.bullets"
        :key="'bullet-' + bulletIndex"
        v-html="bulletText"
      ></li>
    </ul>
    <!-- eslint-enable -->
    <server-specified-property-control
      v-else-if="element.element_type === 'property_control'"
      :property-data="element"
      @saved="refreshOnSaved && $emit('requestUpdate')"
      @changed="propertyChanged(index, $event)"
    />
    <server-specified-action-button
      v-else-if="element.element_type === 'action_button'"
      :action-data="element"
      @request-update="$emit('requestUpdate')"
    />
    <simple-accordion
      v-else-if="element.element_type === 'accordion'"
      :title="element.title"
      :highlighted="changedProperties[index]"
    >
      <server-specified-interface
        :elements="element.children"
        :refresh-on-saved="refreshOnSaved"
        @request-update="$emit('requestUpdate')"
        @changed="propertyChanged(index, $event)"
      />
    </simple-accordion>
    <div v-else-if="element.element_type === 'container'" :class="element.css_class">
      <server-specified-interface
        :elements="element.children"
        :refresh-on-saved="refreshOnSaved"
        @request-update="$emit('requestUpdate')"
        @changed="propertyChanged(index, $event)"
      />
    </div>
  </div>
</template>

<script>
import SimpleAccordion from "../genericComponents/simpleAccordion.vue";
import ServerSpecifiedPropertyControl from "./serverSpecifiedPropertyControl.vue";
import ServerSpecifiedActionButton from "./serverSpecifiedActionButton.vue";
import CompactHelp from "../genericComponents/compactHelp.vue";

export default {
  name: "ServerSpecifiedInterface",

  components: {
    SimpleAccordion,
    ServerSpecifiedPropertyControl,
    ServerSpecifiedActionButton,
    CompactHelp,
  },

  props: {
    refreshOnSaved: { type: Boolean, default: false },
    elements: {
      type: Array,
      required: true,
    },
  },

  emits: ["requestUpdate", "changed"],

  data() {
    return {
      changedProperties: {},
    };
  },

  computed: {
    settingsChanged() {
      return Object.values(this.changedProperties).some(Boolean);
    },
  },

  watch: {
    settingsChanged: {
      immediate: true,
      handler(val) {
        this.$emit("changed", val);
      },
    },
  },

  methods: {
    propertyChanged(index, changed) {
      this.changedProperties[index] = changed;
    },
  },
};
</script>

<style lang="less"></style>
