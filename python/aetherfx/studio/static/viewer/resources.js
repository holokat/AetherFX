/* Textures, meshes and material descriptions for one opened effect.
 *
 * The resources message names them; the bytes come from
 * ``/api/stream/texture/<id>.png`` and ``/api/stream/mesh/<id>.json``.  Both
 * load asynchronously: a frame that references something still in flight simply
 * draws untextured until it lands, which is better than stalling playback.
 */

import * as THREE from 'three';

export class ResourceSet {
  constructor(renderer) {
    this.renderer = renderer;
    this.loader = new THREE.TextureLoader();
    this.textures = new Map();          // id -> THREE.Texture (once loaded)
    this.infos = new Map();             // id -> {width, height, frames, frame_width, url}
    this.meshes = new Map();            // id -> [BufferGeometry] (one per variant)
    this.materials = {};
    this.effect = {};
    this.renderSettings = {};
    this.camera = null;
    this.generation = 0;
  }

  /* Take a new resources message.  Assets are swapped in as they arrive rather
   * than cleared first: the frontend re-opens the effect after every parameter
   * edit, and dropping the textures in between would flash the whole scene
   * untextured on each keystroke. */
  load(message) {
    const generation = ++this.generation;
    this.materials = message.materials || {};
    this.effect = message.effect || {};
    this.renderSettings = message.render_settings || {};
    this.camera = message.camera || null;

    const textures = message.textures || {};
    const meshes = message.meshes || {};
    this.dropMissing(this.textures, textures, (texture) => texture.dispose());
    this.dropMissing(this.meshes, meshes, (list) => list.forEach((geometry) => geometry.dispose()));
    this.infos.clear();

    Object.keys(textures).forEach((id) => {
      const info = textures[id] || {};
      this.infos.set(id, info);
      if (!info.url) return;
      const sheet = (info.frames || 1) > 1;
      this.loader.load(info.url, (texture) => {
        if (generation !== this.generation) { texture.dispose(); return; }
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.wrapS = texture.wrapT = THREE.ClampToEdgeWrapping;
        texture.magFilter = THREE.LinearFilter;
        // A frame strip must not be mipmapped: the engine lays its frames side by
        // side in one image, so mip level 2 and beyond average neighbouring frames
        // together and a 24-frame flame turns into orange fog.
        texture.generateMipmaps = !sheet;
        texture.minFilter = sheet ? THREE.LinearFilter : THREE.LinearMipmapLinearFilter;
        texture.anisotropy = sheet ? 1 : Math.min(4, this.renderer.capabilities.getMaxAnisotropy());
        texture.needsUpdate = true;
        const previous = this.textures.get(id);
        this.textures.set(id, texture);
        if (previous && previous !== texture) previous.dispose();
      }, undefined, () => { /* a missing texture just means untextured */ });
    });

    Object.keys(meshes).forEach((id) => {
      const info = meshes[id] || {};
      if (!info.url) return;
      fetch(info.url, { cache: 'no-store' })
        .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
        .then((payload) => {
          if (generation !== this.generation) return;
          const previous = this.meshes.get(id);
          this.meshes.set(id, buildGeometries(payload));
          if (previous) previous.forEach((geometry) => geometry.dispose());
        })
        .catch(() => { /* an absent mesh draws nothing */ });
    });
  }

  /* Dispose what the new message no longer mentions. */
  dropMissing(current, next, disposeOne) {
    [...current.keys()].forEach((id) => {
      if (Object.prototype.hasOwnProperty.call(next, id)) return;
      disposeOne(current.get(id));
      current.delete(id);
    });
  }

  texture(id) { return id ? (this.textures.get(id) || null) : null; }
  textureInfo(id) { return id ? (this.infos.get(id) || null) : null; }
  meshGeometries(id) { return id ? (this.meshes.get(id) || null) : null; }

  disposeAssets() {
    this.textures.forEach((texture) => texture.dispose());
    this.textures.clear();
    this.infos.clear();
    this.meshes.forEach((list) => list.forEach((geometry) => geometry.dispose()));
    this.meshes.clear();
  }

  dispose() { this.generation++; this.disposeAssets(); }
}

function buildGeometry(block) {
  const geometry = new THREE.BufferGeometry();
  const positions = block.positions || [];
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3));
  if (block.normals && block.normals.length === positions.length) {
    geometry.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(block.normals), 3));
  }
  if (block.uvs && block.uvs.length === (positions.length / 3) * 2) {
    geometry.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(block.uvs), 2));
  }
  if (block.indices && block.indices.length) {
    geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(block.indices), 1));
  }
  if (!geometry.getAttribute('normal')) geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return geometry;
}

/* ``{positions, normals, uvs, indices, variants:[...]}`` -> one geometry per
 * variant.  Variant 0 is the base block when the source ships no variant list. */
function buildGeometries(payload) {
  const variants = Array.isArray(payload.variants) ? payload.variants : [];
  if (!variants.length) return [buildGeometry(payload)];
  return variants.map((variant) => buildGeometry(variant.positions && variant.positions.length ? variant : payload));
}
