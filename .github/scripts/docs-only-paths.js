"use strict";

const DOC_EXACT = new Set([
  ".github/scripts/doc-citation-baseline.txt",
  ".github/scripts/doc-citation-dangling-baseline.txt",
  ".github/scripts/doc-link-baseline.txt",
  "AUTHORS", "COPYING", "LICENSE", "LICENSE.txt", "NOTICE", "mkdocs.yml",
]);

// Machine-readable data files under docs/ are not prose: tests parse them
// (e.g. tests/architecture/test_ui_radio_control_contract.py reads
// docs/internals/ui-radio-control-contract.toml, and
// tests/test_validation_templates.py globs docs/validation/templates/*.json).
// A change to one must get the normal quick run, not a synthetic green
// status. Mirrors DOCS_DATA_SUFFIXES in classify-quick-paths.py and
// base-gate-policy-v1.js plus the "!docs/**" negations in quick.yml.
const DOCS_DATA_SUFFIXES = new Set([".json", ".toml", ".yaml", ".yml"]);

function isDocumentation(path) {
  if (typeof path !== "string" || !path || path.startsWith("/") || path.split("/").includes("..")) return false;
  const parts = path.split("/");
  const dot = path.lastIndexOf(".");
  const suffix = dot === -1 ? "" : path.slice(dot);
  const asciiLowerSuffix = suffix.replace(/[A-Z]/g, (char) =>
    String.fromCharCode(char.charCodeAt(0) + 32),
  );
  if (parts.length > 1 && parts[0] === "docs" && DOCS_DATA_SUFFIXES.has(asciiLowerSuffix)) return false;
  const inDocsDirectory = parts.length > 1 && (parts[0] === "docs" || parts[0] === ".claude");
  return inDocsDirectory || asciiLowerSuffix === ".md" || asciiLowerSuffix === ".rst" || DOC_EXACT.has(path);
}

function isDocumentationFile({filename, previous_filename: previousFilename}) {
  return isDocumentation(filename) && (previousFilename === undefined || isDocumentation(previousFilename));
}

function allDocumentationFiles(files) {
  return Array.isArray(files) && files.length > 0 && files.every(isDocumentationFile);
}

module.exports = {DOC_EXACT, DOCS_DATA_SUFFIXES, isDocumentation, isDocumentationFile, allDocumentationFiles};
