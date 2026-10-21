// Renders the sample homework pictures used by the display's "Check a photo of the page" request.
//   node tools/site/make_samples.mjs            (needs Chrome and network access to Google Fonts, like tools/cdp.mjs)
// Each sheet in tools/site/sample_sheet.html becomes a 1200x1600 JPEG in algebraic_compiler/web/alexa/samples/.
// The pictures are RENDERED samples of a fictional child's work, not camera photos. Prints each line's true box
// ([left, top, right, bottom] on the 0-1000 scale) so a model's reading can be compared against it.
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const samples = join(root, 'algebraic_compiler/web/alexa/samples');
mkdirSync(samples, { recursive: true });
const sheets = { 'maya-homework.jpg': 'work', 'maya-homework-unclear.jpg': 'unclear' };
for (const [file, hash] of Object.entries(sheets)) {
  const tmp = mkdtempSync(join(tmpdir(), 'syw-sheet-'));
  const plan = { url: `file://${join(root, 'tools/site/sample_sheet.html')}#${hash}`, width: 1200, height: 1600, out: tmp, settle: 2500, quality: 82,
    steps: [{ eval: 'document.fonts.ready.then(() => 1)' }, { wait: 500 }, { eval: 'JSON.stringify(window.workBoxes())', log: true }, { shot: file }] };
  writeFileSync(join(tmp, 'plan.json'), JSON.stringify(plan));
  execFileSync('node', [join(root, 'tools/cdp.mjs'), join(tmp, 'plan.json')], { stdio: 'inherit' });
  copyFileSync(join(tmp, file), join(samples, file));
  console.log(`${file}: ${Math.round(statSync(join(samples, file)).size / 1024)} KB, line boxes ${readFileSync(join(tmp, 'console.log'), 'utf8')}`);
  rmSync(tmp, { recursive: true, force: true });
}
