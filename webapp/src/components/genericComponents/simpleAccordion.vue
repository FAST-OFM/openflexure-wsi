<template>
  <ul uk-accordion="multiple: true">
    <li>
      <a class="uk-accordion-title" :class="{ 'ofm-highlighted-title': highlighted }" href="#">
        {{ title }}
      </a>
      <div class="uk-accordion-content">
        <slot />
      </div>
    </li>
  </ul>
</template>
<script>
export default {
  name: "SimpleAccordion",

  props: {
    title: {
      type: String,
      required: true,
    },
    /* Set to true if the contents of the accordion are in a modified state.*/
    highlighted: {
      type: Boolean,
      default: false,
    },
  },
};
</script>

<style lang="less"></style>
