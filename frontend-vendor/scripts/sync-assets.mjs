import {mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const workspaceRoot = path.resolve(__dirname, '..', '..');
const thirdpartyDir = path.join(workspaceRoot, 'django', 'otto', 'static', 'thirdparty');
const metadataPath = path.join(workspaceRoot, 'frontend-vendor', 'vendor-manifest.json');

const packages = [
  {
    name: 'docx-preview',
    sourceRelativePath: path.join('dist', 'docx-preview.min.js'),
    targetBasename: 'docx-preview',
  },
  {
    name: 'jszip',
    sourceRelativePath: path.join('dist', 'jszip.min.js'),
    targetBasename: 'jszip',
  },
];

mkdirSync(thirdpartyDir, {recursive: true});

const manifest = {
  assets: [],
};

for (const pkg of packages) {
  const packageRoot = path.join(workspaceRoot, 'node_modules', pkg.name);
  const packageJsonPath = path.join(packageRoot, 'package.json');
  const packageJson = JSON.parse(readFileSync(packageJsonPath, 'utf8'));
  const version = packageJson.version;
  const sourcePath = path.join(packageRoot, pkg.sourceRelativePath);
  const targetFilename = `${pkg.targetBasename}-${version}.min.js`;
  const targetPath = path.join(thirdpartyDir, targetFilename);

  for (const entry of readdirSync(thirdpartyDir)) {
    if (entry.startsWith(`${pkg.targetBasename}-`) && entry.endsWith('.min.js')) {
      rmSync(path.join(thirdpartyDir, entry), {force: true});
    }
  }

  const sourceContents = readFileSync(sourcePath, 'utf8').replace(
    /\n\/\/# sourceMappingURL=.*$/m,
    ''
  );
  writeFileSync(targetPath, `${sourceContents}\n`);

  manifest.assets.push({
    packageName: pkg.name,
    version,
    sourcePath: path.relative(workspaceRoot, sourcePath),
    outputPath: path.relative(workspaceRoot, targetPath),
  });
}

writeFileSync(metadataPath, `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`Synced ${manifest.assets.length} browser asset(s).`);
for (const asset of manifest.assets) {
  console.log(`- ${asset.packageName}@${asset.version} -> ${asset.outputPath}`);
}
