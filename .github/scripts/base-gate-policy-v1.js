'use strict';

const SHA_PATTERN = /^[0-9a-f]{40}$/u;
const DOC_SUFFIXES = new Set(['.md', '.rst']);
const DOC_EXACT = new Set([
  '.github/scripts/doc-citation-baseline.txt',
  '.github/scripts/doc-citation-dangling-baseline.txt',
  '.github/scripts/doc-link-baseline.txt',
  'AUTHORS',
  'COPYING',
  'LICENSE',
  'LICENSE.txt',
  'NOTICE',
  'mkdocs.yml',
]);
const CORE_EXACT = new Set(['.importlinter', 'pyproject.toml', 'uv.lock']);
const CI_EXACT = new Set(['tests/test_ci_path_filters.py']);

function assertSha(value, label) {
  if (typeof value !== 'string' || !SHA_PATTERN.test(value)) {
    throw new Error(`${label} must be an exact lowercase commit SHA`);
  }
  return value;
}

function pathParts(path) {
  if (
    typeof path !== 'string' ||
    path.length === 0 ||
    path.startsWith('/') ||
    path.includes('\\') ||
    path.includes('\0')
  ) {
    throw new Error(`invalid repository path: ${JSON.stringify(path)}`);
  }
  const parts = path.split('/');
  if (parts.some((part) => part.length === 0 || part === '.' || part === '..')) {
    throw new Error(`invalid repository path: ${JSON.stringify(path)}`);
  }
  return parts;
}

function isDocumentation(path) {
  const parts = pathParts(path);
  const dot = path.lastIndexOf('.');
  const suffix = dot === -1 ? '' : path.slice(dot).toLowerCase();
  return (
    (parts.length > 1 && (parts[0] === 'docs' || parts[0] === '.claude')) ||
    DOC_SUFFIXES.has(suffix) ||
    DOC_EXACT.has(path)
  );
}

function isCiControl(path) {
  const parts = pathParts(path);
  return (
    CI_EXACT.has(path) ||
    (parts.length >= 2 &&
      parts[0] === '.github' &&
      (parts[1] === 'scripts' || parts[1] === 'workflows') &&
      !isDocumentation(path))
  );
}

function isFrontend(path) {
  const parts = pathParts(path);
  return (
    !isDocumentation(path) &&
    (parts[0] === 'frontend' ||
      (parts[0] === 'src' && parts[1] === 'rigplane' && parts[2] === 'web'))
  );
}

function isCore(path) {
  const parts = pathParts(path);
  return (
    !isDocumentation(path) &&
    !isCiControl(path) &&
    (['src', 'tests', 'rigs', 'contracts'].includes(parts[0]) ||
      CORE_EXACT.has(path))
  );
}

function classifyPullFiles(files, changedFiles) {
  if (!Number.isSafeInteger(changedFiles) || changedFiles <= 0 || changedFiles >= 3000) {
    throw new Error('pull request changed_files count is outside the trusted range');
  }
  if (!Array.isArray(files) || files.length !== changedFiles) {
    throw new Error('pull request file enumeration is incomplete');
  }

  const paths = [];
  for (const file of files) {
    if (file === null || typeof file !== 'object') {
      throw new Error('pull request file entry is invalid');
    }
    paths.push(file.filename);
    if (file.previous_filename !== undefined) {
      paths.push(file.previous_filename);
    }
  }
  const unique = [...new Set(paths)];
  unique.forEach(pathParts);
  if (unique.length === 0) {
    throw new Error('pull request contains no classifiable paths');
  }

  const docs = unique.every(isDocumentation);
  const ci = unique.some(isCiControl);
  const frontend = unique.some(isFrontend);
  let core = unique.some(isCore);
  const hasUnknown = unique.some(
    (path) =>
      !isDocumentation(path) &&
      !isCiControl(path) &&
      !isFrontend(path) &&
      !isCore(path),
  );
  // Unknown non-documentation roots receive the broad Python/core carrier.
  // This is deliberately allocation-heavy rather than allowing an uncovered
  // path to publish success.
  core ||= hasUnknown;

  if (!docs && !core && !frontend && !ci) {
    throw new Error('non-documentation change selected no substantive carrier');
  }
  return {core, frontend, ci, docs};
}

module.exports = {
  assertSha,
  classifyPullFiles,
  isDocumentation,
};
