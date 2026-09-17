/* The live GPU viewer: a three.js renderer fed by /ws/stream.
 *
 * Every frame message from the engine becomes scene state; the renderer draws
 * at display rate regardless of the stream rate, so orbiting stays smooth even
 * while playback is paused.  Post is HDR throughout: RenderPass -> heat haze ->
 * UnrealBloom -> OutputPass (ACES) -> SMAA.
 *
 * docs/GPU_VIEWER.md explains the architecture and how to extend it.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { SMAAPass } from 'three/addons/postprocessing/SMAAPass.js';
import { LuminosityHighPassShader } from 'three/addons/shaders/LuminosityHighPassShader.js';

import { StreamClient } from './protocol.js';
import { HeatHazeShader, MAX_LIGHTS, installBloomClamp, makeNoiseTexture } from './shaders.js';
import { LAYER_OPAQUE, LAYER_TRANSPARENT, ParticleRenderer, makeSharedUniforms } from './particles.js';
import { DecalRenderer, RibbonRenderer } from './ribbons.js';
import { MeshInstanceRenderer, STAGE_DEFAULTS, Stage } from './scene.js';
import { VolumeRenderer } from './volumes.js';
import { ResourceSet } from './resources.js';

/* How bright a pixel may be before the bloom pyramid stops caring (see
 * installBloomClamp()).  The CPU reference renderer blurs its bright pass with a
 * bounded gaussian - sigma = bloom_radius * width/2, so a few dozen pixels -
 * while UnrealBloomPass always spreads down to a 1/32 mip, i.e. the whole frame.
 * Capping the input is what keeps the two in the same family: measured against
 * /api/frame on fire_aoe, 3 keeps the frame corners dark while the core still
 * glows. */
const BLOOM_CLAMP = 3.0;

const STUDIO_ZOOM_OUT = 1.45;          /* the studio frames a little wider than the effect's camera */
const DEFAULT_CAMERA = { position: [0, 1.5, 5], target: [0, 1, 0], up: [0, 1, 0], fov: 45 };

export class GLViewer {
  constructor(canvas, options) {
    options = options || {};
    this.canvas = canvas;
    this.container = options.container || canvas.parentElement;
    this.onTime = options.onTime || function () {};
    this.onStats = options.onStats || function () {};
    this.onStatus = options.onStatus || function () {};

    this.renderer = new THREE.WebGLRenderer({
      canvas: canvas, antialias: false, alpha: false, stencil: false,
      powerPreference: 'high-performance', preserveDrawingBuffer: true
    });
    this.renderer.setClearColor(0x000000, 1);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.autoClear = true;
    // The composer renders many times per frame and info.render resets on every
    // one of them, so the viewer accumulates the counters itself.
    this.renderer.info.autoReset = false;
    // Glass meshes (transmission) make three.js re-render the opaque scene into a refraction buffer every
    // frame. That buffer is only ever sampled blurred by the material's roughness, so half resolution
    // looks the same at a quarter of the fill: the glassy ice effects were the slowest in the library.
    this.renderer.transmissionResolutionScale = 0.5;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.05, 600);
    this.camera.layers.enable(LAYER_OPAQUE);
    this.camera.layers.enable(LAYER_TRANSPARENT);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.screenSpacePanning = true;
    this.controls.minDistance = 0.2;
    this.controls.maxDistance = 200;
    // Same pitch limits the old image viewport had (+-1.45 rad from horizontal).
    this.controls.minPolarAngle = Math.PI / 2 - 1.45;
    this.controls.maxPolarAngle = Math.PI / 2 + 1.45;
    this.controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
    this.installShiftPan();

    this.noiseTexture = makeNoiseTexture(64);
    this.shared = makeSharedUniforms();
    this.resources = new ResourceSet(this.renderer);
    this.stage = new Stage(this.scene);
    this.particles = new ParticleRenderer(this.scene, this.shared, this.noiseTexture);
    this.ribbons = new RibbonRenderer(this.scene);
    this.decals = new DecalRenderer(this.scene);
    this.meshInstances = new MeshInstanceRenderer(this.scene);
    this.volumes = new VolumeRenderer(this.scene, this.shared);

    this.depthTarget = null;
    this.composer = null;
    this.renderMode = null;            /* which rung of the pipeline fallback we landed on */
    this.contextLost = false;
    this.buildComposer();

    this.frame = null;
    this.frameIsNew = false;
    this.duration = 0;
    this.playing = false;
    this.resolutionScale = 'fit';
    this.cameraDirty = true;
    this.lastStatsAt = 0;
    this.fpsAverage = 0;
    this.particleCount = 0;
    this.running = false;
    this.started = false;
    this.hasResources = false;
    this.keepCameraOnResources = false;
    this.effectCamera = null;

    this.client = new StreamClient({
      url: (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/stream',
      onFrame: (frame) => this.receiveFrame(frame),
      onResources: (message) => this.receiveResources(message),
      onState: (state) => { this.playing = !!state.playing; this.onStatus({ kind: 'state', state: state }); },
      onError: (error) => this.onStatus({ kind: 'error', error: error }),
      onOpen: () => this.onStatus({ kind: 'connected' }),
      onClose: () => this.onStatus({ kind: 'disconnected' })
    });

    this.resizeObserver = typeof ResizeObserver === 'function'
      ? new ResizeObserver(() => this.resize())
      : null;
    if (this.resizeObserver && this.container) this.resizeObserver.observe(this.container);
    else window.addEventListener('resize', () => this.resize());

    this.installContextHandlers();
  }

  /* Shift + left drag pans, matching the studio's old image viewport. */
  installShiftPan() {
    const controls = this.controls;
    this.canvas.addEventListener('pointerdown', function (event) {
      if (event.button === 0) controls.mouseButtons.LEFT = event.shiftKey ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    }, true);
  }

  // -- pipeline -------------------------------------------------------

  /* The scene target, best format first.
   *
   * 4x MSAA on the scene pass.  SMAA alone runs *after* tone mapping and barely
   * touches a bright mesh silhouette on a dark background - an ice crystal edge
   * stair-steps because the HDR values either side of it differ by an order of
   * magnitude.  Multisampling the HDR target fixes it at the source; three.js
   * resolves the buffer when a pass reads its texture.  `depthTarget` stays
   * single-sampled: the soft-particle and volume passes sample it directly as a
   * depth texture, which a multisampled attachment cannot be.
   *
   * Not every context can do that.  Rendering *into* a half-float buffer is an
   * extension in WebGL2 (a browser running on a software rasteriser, or with
   * the GPU blocklisted, often has neither), and MAX_SAMPLES can be 0.  Each
   * rung below costs image quality; none of them costs the viewer, which is the
   * point - a studio that silently drops to the CPU reference frames looks like
   * the renderer regressed.
   */
  targetModes() {
    const gl = this.renderer.getContext();
    const float = !!(gl.getExtension('EXT_color_buffer_float') || gl.getExtension('EXT_color_buffer_half_float'));
    let samples = 0;
    try { samples = gl.getParameter(gl.MAX_SAMPLES) | 0; } catch (err) { samples = 0; }
    const modes = [];
    if (float && samples >= 4) {
      modes.push({ samples: 4, type: THREE.HalfFloatType, tone: 'ok', label: 'GPU · MSAA 4x', detail: 'GPU · MSAA 4x · HDR' });
    }
    if (float) {
      modes.push({ samples: 0, type: THREE.HalfFloatType, tone: 'warn', label: 'GPU · no MSAA', detail: 'GPU · no MSAA · HDR' });
    }
    modes.push({ samples: 0, type: THREE.UnsignedByteType, tone: 'warn', label: 'GPU · no MSAA', detail: 'GPU · no MSAA · 8-bit' });
    return modes;
  }

  buildComposer() {
    const size = this.drawingSize();
    this.rebuildDepthTarget(size);              // the probe render samples it
    const modes = this.targetModes();
    let lastError = null;
    for (let i = 0; i < modes.length; i++) {
      try {
        this.assembleComposer(modes[i], size);
        this.probeComposer();
        this.renderMode = modes[i];
        if (lastError) console.warn('[aetherfx viewer] ' + lastError + ' - falling back to ' + modes[i].detail);
        return;
      } catch (err) {
        lastError = modes[i].detail + ' failed (' + ((err && err.message) || err) + ')';
        this.disposeComposer();
      }
    }
    throw new Error('no usable render target: ' + (lastError || 'unknown'));
  }

  assembleComposer(mode, size) {
    const target = new THREE.WebGLRenderTarget(size.width, size.height, {
      type: mode.type, colorSpace: THREE.LinearSRGBColorSpace, depthBuffer: true, samples: mode.samples
    });
    this.composer = new EffectComposer(this.renderer, target);
    this.composer.addPass(new RenderPass(this.scene, this.camera));

    this.hazePass = new ShaderPass(HeatHazeShader);
    this.hazePass.enabled = false;
    this.composer.addPass(this.hazePass);

    this.bloomPass = new UnrealBloomPass(new THREE.Vector2(size.width, size.height), 0.5, 0.4, 1.0);
    installBloomClamp(this.bloomPass, LuminosityHighPassShader, BLOOM_CLAMP);
    this.composer.addPass(this.bloomPass);

    this.composer.addPass(new OutputPass());
    this.smaaPass = new SMAAPass();
    this.composer.addPass(this.smaaPass);

    if (mode.type !== THREE.HalfFloatType) this.demoteHalfFloatTargets(mode.type);
  }

  /* The addon passes allocate half-float buffers of their own - the bloom mip
   * pyramid and its bright pass, SMAA's edge and weight targets - with the type
   * hard-coded.  A context that cannot render into a half-float buffer cannot
   * render into those either, so without this the 8-bit rung of the fallback is
   * not actually reachable: the scene target is fine and the post chain fails
   * with INVALID_FRAMEBUFFER_OPERATION.  Walking the passes rather than naming
   * their fields keeps this working across a three.js upgrade. */
  demoteHalfFloatTargets(type) {
    let changed = 0;
    const visit = function (value, depth) {
      if (!value || typeof value !== 'object' || depth > 2) return;
      if (Array.isArray(value)) { value.forEach(function (item) { visit(item, depth + 1); }); return; }
      if (!value.isWebGLRenderTarget) return;
      (value.textures || [value.texture]).forEach(function (texture) {
        if (texture && texture.type === THREE.HalfFloatType) { texture.type = type; changed++; }
      });
    };
    this.composer.passes.forEach(function (pass) {
      Object.keys(pass).forEach(function (key) { visit(pass[key], 0); });
    });
    return changed;
  }

  /* Allocate the target for real and draw one frame through the whole chain.
   * A driver that advertises a format it cannot actually attach fails here -
   * at boot, where there is still a cheaper rung to drop to - instead of
   * leaving a black viewport for the rest of the session. */
  probeComposer() {
    const gl = this.renderer.getContext();
    for (let guard = 0; guard < 32 && gl.getError() !== gl.NO_ERROR; guard++) { /* drain older errors */ }

    this.renderer.setRenderTarget(this.composer.renderTarget1);
    const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
    this.renderer.setRenderTarget(null);
    if (status !== gl.FRAMEBUFFER_COMPLETE) throw new Error('framebuffer incomplete 0x' + status.toString(16));

    this.composer.render();
    // Only the errors that mean "this pipeline cannot draw" count; INVALID_ENUM
    // and INVALID_VALUE come from capability probing and are harmless here.
    const error = gl.getError();
    if (error === gl.INVALID_FRAMEBUFFER_OPERATION || error === gl.OUT_OF_MEMORY || error === gl.CONTEXT_LOST_WEBGL) {
      throw new Error('gl error 0x' + error.toString(16));
    }
  }

  /* EffectComposer.dispose() frees only its two buffers and the internal copy
   * pass, so the passes this viewer added are freed here.
   *
   * `stale` means the GL objects behind them died with a lost context: the
   * driver has already freed every one, and deleting the old handles through
   * the restored context logs a couple of hundred `INVALID_OPERATION: delete:
   * object does not belong to this context` warnings for nothing.  Dropping
   * the references is the whole job - three.js threw away its own maps in
   * initGLContext(), so nothing is left pointing at them either. */
  disposeComposer(stale) {
    if (!this.composer) return;
    if (!stale) {
      this.composer.passes.forEach(function (pass) {
        if (!pass || typeof pass.dispose !== 'function') return;
        try { pass.dispose(); } catch (err) { /* half-built */ }
      });
      try { this.composer.dispose(); } catch (err) { /* ditto */ }
    }
    this.composer = null;
    this.hazePass = null;
    this.bloomPass = null;
    this.smaaPass = null;
  }

  rebuildDepthTarget(size) {
    if (this.depthTarget) this.depthTarget.dispose();
    const depthTexture = new THREE.DepthTexture(size.width, size.height);
    depthTexture.type = THREE.UnsignedIntType;
    depthTexture.format = THREE.DepthFormat;
    depthTexture.minFilter = THREE.NearestFilter;
    depthTexture.magFilter = THREE.NearestFilter;
    this.depthTarget = new THREE.WebGLRenderTarget(size.width, size.height, {
      depthTexture: depthTexture, depthBuffer: true,
      minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter
    });
    this.shared.uDepth.value = depthTexture;
    this.shared.uResolution.value.set(size.width, size.height);
  }

  /* Canvas is always the viewport's CSS size; `size` picks the pixel ratio. */
  pixelRatio() {
    const device = Math.min(window.devicePixelRatio || 1, 2);
    const scale = this.resolutionScale;
    if (scale === 'fit' || scale === undefined || scale === null) return device;
    if (scale === '1x') return 1;
    if (scale === '0.5x') return 0.5;
    const numeric = parseFloat(scale);
    if (isFinite(numeric) && numeric > 0) {
      const box = this.cssSize();
      return Math.max(0.25, Math.min(2, numeric / Math.max(box.width, box.height)));
    }
    return device;
  }

  cssSize() {
    const element = this.container || this.canvas;
    return {
      width: Math.max(16, element.clientWidth || 640),
      height: Math.max(16, element.clientHeight || 360)
    };
  }

  drawingSize() {
    const css = this.cssSize();
    const ratio = this.pixelRatio();
    return { width: Math.max(8, Math.round(css.width * ratio)), height: Math.max(8, Math.round(css.height * ratio)) };
  }

  resize() {
    // Nothing to size while the context is gone; restoreContext() resizes once
    // the pipeline is back.
    if (this.contextLost || !this.composer) return;
    const css = this.cssSize();
    const ratio = this.pixelRatio();
    this.renderer.setPixelRatio(ratio);
    this.renderer.setSize(css.width, css.height, false);
    this.composer.setPixelRatio(ratio);
    this.composer.setSize(css.width, css.height);
    this.camera.aspect = css.width / css.height;
    this.camera.updateProjectionMatrix();
    this.rebuildDepthTarget(this.drawingSize());
  }

  // -- stream ---------------------------------------------------------

  start() {
    if (this.started) return;
    this.started = true;
    this.client.connect();
    this.resize();
    this.startLoop();
  }

  startLoop() {
    if (this.running) return;
    this.running = true;
    this.lastFrameAt = 0;               // do not average a context-loss gap into the fps
    const loop = (now) => {
      if (!this.running) return;
      // One bad frame must not stop the viewer: report it once and keep drawing.
      try {
        this.renderFrame(now);
      } catch (err) {
        this.lastError = err;
        if (!this.reportedError) {
          this.reportedError = true;
          console.error('[aetherfx viewer] frame failed', err);
          this.onStatus({ kind: 'error', error: { code: 'frame_failed', message: String((err && err.message) || err) } });
        }
      }
      this.rafHandle = requestAnimationFrame(loop);
    };
    this.rafHandle = requestAnimationFrame(loop);
  }

  stopLoop() {
    this.running = false;
    if (this.rafHandle) { cancelAnimationFrame(this.rafHandle); this.rafHandle = null; }
  }

  // -- context loss ---------------------------------------------------

  /* A Chromium tab loses its GL context on a GPU process restart, a driver
   * reset, or when the browser reclaims a background tab's contexts.  Left
   * alone the canvas goes black and stays black for the rest of the session,
   * which reads as a rendering regression rather than as what it is.
   *
   * three.js registers its own handlers inside the WebGLRenderer constructor,
   * i.e. before these, so they run first: it re-initialises its GL state and
   * then re-uploads geometries, attributes, textures and programs lazily from
   * the CPU-side copies it still holds (that covers every layer - particles,
   * ribbons, mesh instances, decals, volumes).  What it cannot know about is
   * the pipeline this viewer built on top of it, which is what restoreContext()
   * puts back. */
  installContextHandlers() {
    this.canvas.addEventListener('webglcontextlost', (event) => {
      // Without preventDefault() the browser never fires webglcontextrestored.
      event.preventDefault();
      if (this.contextLost) return;
      this.contextLost = true;
      this.stopLoop();
      this.volumes.disposeTarget();      // the reduced volume buffer died with the context; it is rebuilt on demand
      console.warn('[aetherfx viewer] WebGL context lost - render loop paused until the browser restores it');
      this.onStatus({ kind: 'context_lost' });
    }, false);

    this.canvas.addEventListener('webglcontextrestored', () => {
      if (!this.contextLost) return;
      this.contextLost = false;
      try {
        this.restoreContext();
      } catch (err) {
        console.error('[aetherfx viewer] context restored but the pipeline could not be rebuilt', err);
        this.onStatus({ kind: 'error', error: { code: 'restore_failed', message: String((err && err.message) || err) } });
      }
    }, false);
  }

  restoreContext() {
    // Everything that lived in GL memory went with the context: the composer's
    // buffers, the bloom mip pyramid, SMAA's targets and the depth target the
    // soft-particle and volume shaders sample.  Rebuild them - the whole
    // fallback chain runs again, because a restored context may well be a
    // different (software) one - then push back every piece of state that lives
    // on a pass rather than in the scene graph.
    this.disposeComposer(true);
    this.depthTarget = null;                 // its GL objects went with the context
    this.reportedError = false;
    this.buildComposer();
    this.resize();
    this.applyStageToPipeline();             // exposure, bloom strength / radius / threshold
    if (this.frame) {
      this.frameIsNew = true;                // re-push every layer on the next draw
      this.applyPostEffects(this.frame.post_effects);
    }
    // three.js re-uploads a texture from the image it still holds, so normally
    // there is nothing to fetch.  A source that dropped its pixels (a closed
    // ImageBitmap, a decode that never finished) cannot be re-uploaded from
    // anything, and only then is a resources message worth the round trip and
    // the full asset rebuild it triggers.  Keep the user's camera either way.
    if (this.hasResources && this.texturesNeedReupload()) {
      console.warn('[aetherfx viewer] re-requesting resources: a texture lost its pixels with the context');
      this.keepCameraOnResources = true;
      this.client.resources();
    }
    // Playback never stopped server side - the socket stayed up - so resuming
    // the loop is all it takes to be live again.
    this.startLoop();
    console.info('[aetherfx viewer] WebGL context restored (' + this.renderMode.detail + ')');
    this.onStatus({ kind: 'context_restored', mode: this.renderMode });
  }

  /* True when some loaded texture can no longer be uploaded from its own image. */
  texturesNeedReupload() {
    let stale = false;
    this.resources.textures.forEach(function (texture) {
      const image = texture && texture.image;
      if (!image) { stale = true; return; }
      if (!image.data && !(image.width > 0 && image.height > 0)) stale = true;
    });
    return stale;
  }

  receiveResources(message) {
    this.resources.load(message);
    this.hasResources = true;
    this.duration = (message.effect && message.effect.duration) || 0;
    this.effectCamera = message.camera || null;
    // A re-request made purely to re-upload assets after a context loss must
    // not yank the view back to the effect's framing.
    if (this.keepCameraOnResources) this.keepCameraOnResources = false;
    else this.cameraDirty = true;
    this.stage.apply(message.render_settings || {});
    this.applyStageToPipeline();
    this.onStatus({ kind: 'resources', resources: message, duration: this.duration });
  }

  receiveFrame(frame) {
    this.frame = frame;
    this.frameIsNew = true;
    if (frame.camera) this.effectCamera = frame.camera;
    this.onTime(frame.time || 0, frame.frame || 0, this.duration);
  }

  // -- camera ---------------------------------------------------------

  resetView() {
    const source = this.effectCamera || (this.resources && this.resources.camera) || DEFAULT_CAMERA;
    const target = new THREE.Vector3().fromArray(source.target || DEFAULT_CAMERA.target);
    const position = new THREE.Vector3().fromArray(source.position || DEFAULT_CAMERA.position);
    position.sub(target).multiplyScalar(STUDIO_ZOOM_OUT).add(target);
    this.camera.position.copy(position);
    this.camera.up.fromArray(source.up || DEFAULT_CAMERA.up);
    this.camera.fov = source.fov || DEFAULT_CAMERA.fov;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(target);
    this.controls.update();
    this.cameraDirty = false;
  }

  // -- stage ----------------------------------------------------------

  setStage(settings) {
    this.stage.apply(settings);
    this.applyStageToPipeline();
  }

  applyStageToPipeline() {
    const s = this.stage.settings;
    this.renderer.toneMappingExposure = typeof s.exposure === 'number' ? s.exposure : 1.0;
    this.bloomStrength = Math.max(0, (typeof s.bloom_intensity === 'number' ? s.bloom_intensity : 0.35)) * 1.5;
    // UnrealBloomPass's radius is a mip blend factor in [0, 1], not a screen fraction.
    this.bloomRadius = Math.min(1, Math.max(0, (typeof s.bloom_radius === 'number' ? s.bloom_radius : 0.04) * 10));
    this.bloomPass.strength = this.bloomStrength;
    this.bloomPass.radius = this.bloomRadius;
    this.bloomPass.threshold = 1.0;
    this.bloomPass.enabled = this.bloomStrength > 0.001 && s.bloom !== false;
  }

  applyPostEffects(effects) {
    let haze = null, bloom = null;
    (effects || []).forEach(function (effect) {
      const type = effect.post_type || effect.type;
      if (type === 'heat_haze' || type === 'heat_distortion' || type === 'distortion') haze = effect;
      else if (type === 'bloom') bloom = effect;
    });
    this.hazePass.enabled = !!haze && (haze.intensity || 0) > 0.0001;
    if (haze) {
      this.hazePass.uniforms.uIntensity.value = haze.intensity || 0;
      this.hazePass.uniforms.uFrequency.value = haze.frequency || 6.0;
    }
    if (bloom) {
      this.bloomPass.strength = Math.max(0, (bloom.intensity !== undefined ? bloom.intensity : this.bloomStrength)) * 1.5;
      this.bloomPass.radius = bloom.radius !== undefined ? Math.min(1, Math.max(0, bloom.radius * 10)) : this.bloomRadius;
      if (typeof bloom.threshold === 'number') this.bloomPass.threshold = bloom.threshold;
      this.bloomPass.enabled = this.bloomPass.strength > 0.001;
    } else if (this.bloomPass.strength !== this.bloomStrength) {
      this.applyStageToPipeline();
    }
  }

  // -- drawing --------------------------------------------------------

  /* One context object, reused every frame: the renderers only read from it. */
  frameContext() {
    const resources = this.resources;
    if (!this._context) {
      this._context = {
        camera: this.camera,
        materials: {},
        texture: (id) => resources.texture(id),
        textureInfo: (id) => resources.textureInfo(id),
        meshGeometries: (id) => resources.meshGeometries(id),
        noiseTexture: this.noiseTexture,
        softParticles: true
      };
    }
    this._context.materials = resources.materials || {};
    this._context.softParticles = this.stage.settings.soft_particles !== false;
    return this._context;
  }

  renderFrame(now) {
    if (this.contextLost || !this.composer) return;
    this.controls.update();
    if (this.cameraDirty && this.effectCamera) this.resetView();

    const frame = this.frame;
    if (frame) {
      const context = this.frameContext();
      if (this.frameIsNew) {
        this.frameIsNew = false;
        this.shared.uTime.value = frame.time || 0;
        this.hazePass.uniforms.uTime.value = frame.time || 0;
        this.stage.updateLights(frame);
        this.meshInstances.update(frame, context);
        this.decals.update(frame, context);
        this.volumes.update(frame);
        this.applyPostEffects(frame.post_effects);
        this.particleCount = this.particles.update(frame, context);
      }
      // ribbons face the camera, so they are rebuilt whenever the view moves
      this.camera.updateMatrixWorld();
      this.ribbons.update(frame, context);
      this.stage.fillLightUniforms(frame, this.camera, this.shared, MAX_LIGHTS);
    }

    this.camera.updateMatrixWorld();
    this.shared.uNear.value = this.camera.near;
    this.shared.uFar.value = this.camera.far;

    this.renderer.info.reset();
    this.renderDepthPrepass();
    this.volumes.renderOffscreen(this.renderer, this.camera, this.drawingSize());
    this.composer.render();
    this.reportStats(now);
  }

  /* Opaque-only pass whose depth texture drives the soft-particle fade. */
  renderDepthPrepass() {
    if (!this.depthTarget) return;
    const mask = this.camera.layers.mask;
    this.camera.layers.set(LAYER_OPAQUE);
    this.renderer.setRenderTarget(this.depthTarget);
    this.renderer.render(this.scene, this.camera);   // autoClear clears colour + depth
    this.renderer.setRenderTarget(null);
    this.camera.layers.mask = mask;
  }

  reportStats(now) {
    if (this.lastFrameAt) {
      const delta = now - this.lastFrameAt;
      if (delta > 0) {
        const instant = 1000 / delta;
        this.fpsAverage = this.fpsAverage ? this.fpsAverage * 0.9 + instant * 0.1 : instant;
      }
    }
    this.lastFrameAt = now;
    if (now - this.lastStatsAt < 400) return;
    this.lastStatsAt = now;
    this.onStats({
      fps: this.fpsAverage,
      particles: this.particleCount,
      calls: this.renderer.info.render.calls,
      triangles: this.renderer.info.render.triangles
    });
  }

  snapshot(filename) {
    if (this.contextLost || !this.composer) return;
    // The composer leaves its result on the default framebuffer; render once
    // more first so the canvas is guaranteed to hold the current frame.
    this.renderer.info.reset();
    this.renderDepthPrepass();
    this.volumes.renderOffscreen(this.renderer, this.camera, this.drawingSize());
    this.composer.render();
    const url = this.canvas.toDataURL('image/png');
    const link = document.createElement('a');
    link.href = url;
    link.download = filename || 'aetherfx.png';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  dispose() {
    this.stopLoop();
    this.started = false;
    if (this.resizeObserver) this.resizeObserver.disconnect();
    this.client.close();
    this.particles.dispose();
    this.ribbons.dispose();
    this.decals.dispose();
    this.meshInstances.dispose();
    this.volumes.dispose();
    this.stage.dispose();
    this.resources.dispose();
    this.noiseTexture.dispose();
    if (this.depthTarget) { this.depthTarget.dispose(); this.depthTarget = null; }
    this.disposeComposer();
    this.renderer.dispose();
  }
}

/* ------------------------------------------------------------------ *
 * the facade app.js talks to
 * ------------------------------------------------------------------ */

export function createGLViewer(options) {
  const canvas = options.canvas;
  if (typeof WebGL2RenderingContext === 'undefined') {
    return { available: false, reason: 'WebGL2 is not supported by this browser' };
  }
  let viewer = null;
  try {
    // Do not probe with canvas.getContext() first: the first call fixes the
    // context attributes, and three.js would then silently lose
    // preserveDrawingBuffer (which the snapshot button needs).
    viewer = new GLViewer(canvas, options);
    if (!viewer.renderer.getContext()) throw new Error('WebGL2 is not available');
  } catch (err) {
    // Everything short of "there is no WebGL2 here" has already been retried on
    // a cheaper pipeline by buildComposer(), so reaching this really does mean
    // the CPU frames are the only thing left.
    if (viewer) { try { viewer.dispose(); } catch (cleanup) { /* half-built */ } }
    return { available: false, reason: (err && err.message) || 'WebGL is not available' };
  }
  viewer.start();

  return {
    available: true,
    viewer: viewer,
    renderMode: viewer.renderMode,
    mode: viewer.renderMode ? viewer.renderMode.detail : 'GPU',
    play: function (fps, loop, time, speed) { viewer.client.play(fps || 60, loop !== false, time, speed || 1); },
    pause: function () { viewer.client.pause(); },
    seek: function (time) { viewer.client.seek(time); },
    step: function (frames) { viewer.client.step(frames); },
    reload: function () { viewer.cameraDirty = false; viewer.client.open(); },
    reloadAndFrame: function () { viewer.cameraDirty = true; viewer.client.open(); },
    setStage: function (settings) { viewer.setStage(settings); },
    setResolution: function (scale) { viewer.resolutionScale = scale; viewer.resize(); },
    resetView: function () { viewer.resetView(); },
    snapshot: function (name) { viewer.snapshot(name); },
    duration: function () { return viewer.duration; },
    camera: function () {
      const position = viewer.camera.position, target = viewer.controls.target, up = viewer.camera.up;
      return { position: [position.x, position.y, position.z], target: [target.x, target.y, target.z],
               up: [up.x, up.y, up.z], fov: viewer.camera.fov };
    },
    stageDefaults: STAGE_DEFAULTS,
    dispose: function () { viewer.dispose(); }
  };
}
