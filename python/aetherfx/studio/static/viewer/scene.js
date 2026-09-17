/* The stage the effect plays on: background, ground, grid, lights and the
 * mesh nodes the frame carries.
 *
 * Lights are the one place where the engine and three.js disagree about units.
 * The engine documents "ground value = albedo * intensity / (d^2 + 1)"
 * (docs/VOCABULARY.md); three.js is physical, so a Lambert ground lit by a point
 * light reads `albedo / PI * intensity / d^2`.  Matching the two gives
 * `intensity_three = PI * intensity_engine * d^2/(d^2+1)`, i.e. a constant of PI
 * for anything more than a metre or two away.  `light_scale` is that constant,
 * exposed on the Stage popover so it can be nudged against a reference render.
 */

import * as THREE from 'three';
import { LAYER_OPAQUE, LAYER_TRANSPARENT, colorOf } from './particles.js';

export const STAGE_DEFAULTS = {
  ground_albedo: 0.18,
  background: [0.02, 0.02, 0.025, 1],
  bloom_intensity: 0.35,
  bloom_radius: 0.04,
  exposure: 1.0,
  grid: true,
  light_scale: 3.2,          // ~PI: see the note at the top of this file
  soft_particles: true
};

const GROUND_SIZE = 240;

export class Stage {
  constructor(scene) {
    this.scene = scene;
    this.settings = Object.assign({}, STAGE_DEFAULTS);

    this.groundMaterial = new THREE.MeshStandardMaterial({ color: 0x2e2e2e, roughness: 0.95, metalness: 0.0 });
    this.ground = new THREE.Mesh(new THREE.PlaneGeometry(GROUND_SIZE, GROUND_SIZE), this.groundMaterial);
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.receiveShadow = false;
    this.ground.layers.set(LAYER_OPAQUE);
    scene.add(this.ground);

    this.grid = new THREE.GridHelper(40, 40, 0x3a3a46, 0x24242c);
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.35;
    this.grid.material.depthWrite = false;
    this.grid.position.y = 0.002;
    this.grid.layers.set(LAYER_OPAQUE);
    scene.add(this.grid);

    // A whisper of fill so unlit areas are not pure black voids.
    this.ambient = new THREE.HemisphereLight(0x404860, 0x0a0a0c, 0.25);
    scene.add(this.ambient);

    this.lights = new Map();
  }

  apply(settings) {
    this.settings = Object.assign({}, STAGE_DEFAULTS, this.settings, settings || {});
    const s = this.settings;
    const albedo = typeof s.ground_albedo === 'number' ? s.ground_albedo : 0.18;
    this.groundMaterial.color.setRGB(albedo, albedo, albedo);
    this.ground.visible = s.ground_plane !== false;
    this.grid.visible = s.grid !== false && s.ground_plane !== false;
    const background = s.background || STAGE_DEFAULTS.background;
    this.scene.background = new THREE.Color(background[0], background[1], background[2]);
    return this.settings;
  }

  /* One three.js light per frame light, reused across frames. */
  updateLights(frame) {
    const scale = typeof this.settings.light_scale === 'number' ? this.settings.light_scale : STAGE_DEFAULTS.light_scale;
    const seen = new Set();
    (frame.lights || []).forEach((light) => {
      const type = light.type === 'spot' ? 'spot' : 'point';
      const key = light.id + '|' + type;
      seen.add(key);
      let entry = this.lights.get(key);
      if (!entry) {
        const object = type === 'spot' ? new THREE.SpotLight(0xffffff, 1) : new THREE.PointLight(0xffffff, 1);
        object.castShadow = false;
        this.scene.add(object);
        entry = { object: object, target: null };
        if (type === 'spot') {
          entry.target = new THREE.Object3D();
          this.scene.add(entry.target);
          object.target = entry.target;
        }
        this.lights.set(key, entry);
      }
      const object = entry.object;
      const position = light.position || [0, 0, 0];
      object.position.set(position[0], position[1], position[2]);
      const color = light.color || [1, 1, 1, 1];
      object.color.setRGB(color[0], color[1], color[2]);
      object.intensity = Math.max(0, (light.intensity || 0) * scale);
      object.distance = Math.max(0, light.radius || 0);
      object.decay = 2;
      if (entry.target) {
        const direction = light.direction || [0, -1, 0];
        entry.target.position.set(position[0] + direction[0], position[1] + direction[1], position[2] + direction[2]);
        object.angle = Math.max(0.02, (light.cone_angle || 45) * Math.PI / 180);
        object.penumbra = 0.4;
      }
      object.visible = object.intensity > 0;
    });
    this.lights.forEach((entry, key) => {
      if (seen.has(key)) return;
      this.scene.remove(entry.object);
      if (entry.target) this.scene.remove(entry.target);
      entry.object.dispose();
      this.lights.delete(key);
    });
  }

  /* View-space light uniforms for the particle shader's lit puffs. */
  fillLightUniforms(frame, camera, shared, maxLights) {
    const scale = typeof this.settings.light_scale === 'number' ? this.settings.light_scale : STAGE_DEFAULTS.light_scale;
    const relative = scale / STAGE_DEFAULTS.light_scale;
    const lights = (frame.lights || []).slice(0, maxLights);
    for (let i = 0; i < lights.length; i++) {
      const light = lights[i];
      const position = light.position || [0, 0, 0];
      shared.uLightPosition.value[i].set(position[0], position[1], position[2]).applyMatrix4(camera.matrixWorldInverse);
      const color = light.color || [1, 1, 1, 1];
      // The puff shader already applies the engine's own 1/(d^2+1) falloff, so
      // the gain is the engine intensity with a diffuse constant, tracking the
      // stage slider so puffs and ground brighten together.
      const gain = Math.max(0, light.intensity || 0) * relative * 0.35;
      shared.uLightColor.value[i].setRGB(color[0] * gain, color[1] * gain, color[2] * gain);
      shared.uLightRadius.value[i] = Math.max(0.001, light.radius || 5);
    }
    shared.uLightCount.value = lights.length;
  }

  dispose() {
    this.lights.forEach((entry) => {
      this.scene.remove(entry.object);
      if (entry.target) this.scene.remove(entry.target);
      entry.object.dispose();
    });
    this.lights.clear();
    this.ground.geometry.dispose();
    this.groundMaterial.dispose();
    this.grid.geometry.dispose();
    this.grid.material.dispose();
  }
}

/* ------------------------------------------------------------------ *
 * mesh nodes
 * ------------------------------------------------------------------ */

const TMP_MATRIX = new THREE.Matrix4();

export class MeshInstanceRenderer {
  constructor(scene) {
    this.scene = scene;
    this.instances = new Map();
  }

  update(frame, context) {
    const seen = new Set();
    (frame.mesh_instances || []).forEach((instance) => {
      const geometries = context.meshGeometries(instance.mesh);
      if (!geometries || !geometries.length) return;
      seen.add(instance.id);
      let entry = this.instances.get(instance.id);
      const desc = context.materials[instance.material] || {};
      if (!entry || entry.geometry !== geometries[0]) {
        if (entry) { this.scene.remove(entry.mesh); entry.material.dispose(); }
        const material = new THREE.MeshStandardMaterial({ roughness: 0.6, metalness: 0.05 });
        const mesh = new THREE.Mesh(geometries[0], material);
        mesh.matrixAutoUpdate = false;
        mesh.layers.set(LAYER_OPAQUE);
        this.scene.add(mesh);
        entry = { mesh: mesh, material: material, geometry: geometries[0] };
        this.instances.set(instance.id, entry);
      }
      const transform = instance.transform;
      if (Array.isArray(transform) && transform.length === 16) {
        TMP_MATRIX.fromArray(transform);
        entry.mesh.matrix.copy(TMP_MATRIX);
        entry.mesh.matrixWorldNeedsUpdate = true;
      }
      const tint = colorOf(instance.color, [1, 1, 1]);
      entry.material.color.copy(colorOf(desc.base_color, [1, 1, 1])).multiply(tint);
      const emissive = (typeof instance.emissive === 'number' ? instance.emissive : 0) +
                       (typeof desc.emissive_intensity === 'number' ? desc.emissive_intensity : 0);
      entry.material.emissive.copy(colorOf(desc.emissive_color, [1, 1, 1])).multiplyScalar(emissive);
      const opacity = typeof desc.opacity === 'number' ? desc.opacity : 1;
      entry.material.opacity = opacity;
      entry.material.transparent = opacity < 1;
      entry.mesh.layers.set(opacity < 1 ? LAYER_TRANSPARENT : LAYER_OPAQUE);
      entry.mesh.visible = instance.visible !== false;
    });
    this.instances.forEach((entry, id) => {
      if (seen.has(id)) return;
      this.scene.remove(entry.mesh);
      entry.material.dispose();
      this.instances.delete(id);
    });
  }

  dispose() {
    this.instances.forEach((entry) => { this.scene.remove(entry.mesh); entry.material.dispose(); });
    this.instances.clear();
  }
}
