// stylelint.config.mjs
export default {
  extends: ["stylelint-config-standard"],

  overrides: [
    {
      // VUE FILES OVERRIDE
      files: ["**/*.vue"],
      customSyntax: "postcss-html",
      rules: {
        // Stop Stylelint from throwing errors when it sees
        // LESS functions (like darken, lighten, desaturate) inside Vue files.
        "declaration-property-value-no-unknown": null,
      },
    },
    {
      // LESS FILES OVERRIDE
      files: ["**/*.less"],
      customSyntax: "postcss-less",
      extends: ["stylelint-config-standard-less"],
    },
  ],

  rules: {
    // Global rules
    "selector-class-pattern": null,
    "no-invalid-position-at-import-rule": null,
    "no-empty-source": null,

    // Needed rgba for UIkit color math
    "color-function-notation": null,
    "alpha-value-notation": null,
    "color-function-alias-notation": null,
  },
};
