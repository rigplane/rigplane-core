"use strict";

const DOC_EXACT = new Set([
  ".github/scripts/doc-citation-baseline.txt",
  ".github/scripts/doc-citation-dangling-baseline.txt",
  ".github/scripts/doc-link-baseline.txt",
  "AUTHORS", "COPYING", "LICENSE", "LICENSE.txt", "NOTICE", "mkdocs.yml",
]);

function isDocumentation(path) {
  if (typeof path !== "string" || !path || path.startsWith("/") || path.split("/").includes("..")) return false;
  const parts = path.split("/");
  const dot = path.lastIndexOf(".");
  const suffix = dot === -1 ? "" : path.slice(dot);
  const asciiLowerSuffix = suffix.replace(/[A-Z]/g, (char) =>
    String.fromCharCode(char.charCodeAt(0) + 32),
  );
  const inDocsDirectory = parts.length > 1 && (parts[0] === "docs" || parts[0] === ".claude");
  return inDocsDirectory || asciiLowerSuffix === ".md" || asciiLowerSuffix === ".rst" || DOC_EXACT.has(path);
}

function isDocumentationFile({filename, previous_filename: previousFilename}) {
  return isDocumentation(filename) && (previousFilename === undefined || isDocumentation(previousFilename));
}

function allDocumentationFiles(files) {
  return Array.isArray(files) && files.length > 0 && files.every(isDocumentationFile);
}

module.exports = {DOC_EXACT, isDocumentation, isDocumentationFile, allDocumentationFiles};
