import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const filename = path.join(path.dirname(new URL(import.meta.url).pathname), 'ui-radio-semantic-analyzer.ts');
const source = fs.readFileSync(filename, 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    esModuleInterop: true,
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: filename,
}).outputText;
const moduleRecord = { exports: {} };
const execute = new Function('require', 'module', 'exports', '__filename', '__dirname', compiled);
execute(createRequire(filename), moduleRecord, moduleRecord.exports, filename, path.dirname(filename));

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const output = moduleRecord.exports.analyze(input);
process.stdout.write(`${JSON.stringify(output)}\n`);
