import { useRef, useMemo, useState, Suspense } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import * as THREE from 'three';

// Evaluation signal streams traveling along z-axis, evaluating at z=0, and splitting
function SignalParticleStreams({ isHovered }: { isHovered: boolean }) {
  const pointsRef = useRef<THREE.Points>(null!);
  const count = 320;

  // Initialize particle positions, colors, speeds, and trajectory seeds
  const [positions, colors, speeds, trajectorySeeds] = useMemo(() => {
    const pos = new Float32Array(count * 3);
    const col = new Float32Array(count * 3);
    const spd = new Float32Array(count);
    const seed = new Float32Array(count * 2);

    const passColor = new THREE.Color('#00f2b2'); // Precision Emerald/Cyan
    const failColor = new THREE.Color('#ff4d4d'); // Regression Ember

    const detRand = (n: number) => {
      const v = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
      return v - Math.floor(v);
    };

    for (let i = 0; i < count; i++) {
      const idx = i * 3;
      // 95% pass, 5% regression
      const isRegression = i % 18 === 0;

      // Initial z span along evaluation track (-4.5 to +4.5)
      const z = (detRand(i * 4 + 1) - 0.5) * 9.0;
      const angle = detRand(i * 4 + 2) * Math.PI * 2;
      const radius = 0.15 + detRand(i * 4 + 3) * 0.45;

      pos[idx] = Math.cos(angle) * radius;
      pos[idx + 1] = Math.sin(angle) * radius;
      pos[idx + 2] = z;

      if (isRegression) {
        col[idx] = failColor.r;
        col[idx + 1] = failColor.g;
        col[idx + 2] = failColor.b;
      } else {
        col[idx] = passColor.r;
        col[idx + 1] = passColor.g;
        col[idx + 2] = passColor.b;
      }

      spd[i] = 1.4 + detRand(i * 4 + 4) * 1.8;
      seed[i * 2] = angle;
      seed[i * 2 + 1] = radius;
    }

    return [pos, col, spd, seed];
  }, [count]);

  useFrame((_, delta) => {
    if (!pointsRef.current) return;
    const posAttr = pointsRef.current.geometry.attributes.position as THREE.BufferAttribute;
    const array = posAttr.array as Float32Array;
    const speedMult = isHovered ? 1.45 : 1.0;

    for (let i = 0; i < count; i++) {
      const idx = i * 3;
      const isRegression = i % 18 === 0;
      const baseAngle = trajectorySeeds[i * 2];
      const baseRadius = trajectorySeeds[i * 2 + 1];

      // Move forward along z axis towards observer
      array[idx + 2] += delta * speeds[i] * speedMult;
      const curZ = array[idx + 2];

      if (curZ < 0) {
        // PRE-GATE: Ingesting raw model requests -> converging into central aperture
        const progressToGate = (curZ + 4.5) / 4.5; // 0 to 1
        const r = baseRadius * (1.1 - progressToGate * 0.7);
        array[idx] = Math.cos(baseAngle) * r;
        array[idx + 1] = Math.sin(baseAngle) * r;
      } else {
        // POST-GATE: Classified outcome stream separation
        const exitProgress = curZ / 4.5; // 0 to 1
        if (isRegression) {
          // Regression signal: Deflects outward & upward with turbulent deflection
          const deflectionRadius = 0.35 + exitProgress * 1.75;
          array[idx] = Math.cos(baseAngle) * deflectionRadius;
          array[idx + 1] = Math.sin(baseAngle) * deflectionRadius + exitProgress * 0.5;
        } else {
          // Passing signal: Collimated laminar precision trajectory
          const collimatedRadius = 0.18 + exitProgress * 0.55;
          array[idx] = Math.cos(baseAngle + exitProgress * 0.4) * collimatedRadius;
          array[idx + 1] = Math.sin(baseAngle + exitProgress * 0.4) * collimatedRadius;
        }
      }

      // Recycle particle when exiting bounding field
      if (array[idx + 2] > 4.5) {
        array[idx + 2] = -4.5;
      }
    }

    posAttr.needsUpdate = true;
  });

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
      </bufferGeometry>
      <pointsMaterial
        size={0.055}
        vertexColors
        transparent
        opacity={0.92}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
      />
    </points>
  );
}

// Precision Engineered Collimator Instrument
function InstrumentRig({ isHovered }: { isHovered: boolean }) {
  const outerChamberRef = useRef<THREE.Mesh>(null!);
  const innerGyroRef = useRef<THREE.Mesh>(null!);
  const focalRingRef = useRef<THREE.Mesh>(null!);
  const sensorStrutsRef = useRef<THREE.Group>(null!);

  useFrame((state, delta) => {
    const rate = isHovered ? 1.3 : 0.8;
    // Calm, very slow mechanical rotation
    if (outerChamberRef.current) {
      outerChamberRef.current.rotation.z += delta * 0.12 * rate;
    }
    if (innerGyroRef.current) {
      innerGyroRef.current.rotation.z -= delta * 0.22 * rate;
      innerGyroRef.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.5) * 0.08;
    }
    if (focalRingRef.current) {
      focalRingRef.current.rotation.z += delta * 0.3 * rate;
    }
    if (sensorStrutsRef.current) {
      sensorStrutsRef.current.rotation.z += delta * 0.08 * rate;
    }
  });

  return (
    <group>
      {/* Outer Armor Chassis Torus */}
      <mesh ref={outerChamberRef}>
        <torusGeometry args={[2.0, 0.09, 32, 80]} />
        <meshStandardMaterial
          color="#161922"
          roughness={0.32}
          metalness={0.88}
        />
      </mesh>

      {/* Internal Calibration Gyro Ring */}
      <mesh ref={innerGyroRef} rotation={[0.15, 0.3, 0]}>
        <torusGeometry args={[1.56, 0.045, 24, 64]} />
        <meshStandardMaterial
          color="#283040"
          roughness={0.25}
          metalness={0.85}
        />
      </mesh>

      {/* Central Collimation Aperture Ring */}
      <mesh ref={focalRingRef} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.52, 0.52, 0.08, 36, 1, true]} />
        <meshStandardMaterial
          color="#00f2b2"
          emissive="#00f2b2"
          emissiveIntensity={0.45}
          transparent
          opacity={0.4}
          side={THREE.DoubleSide}
        />
      </mesh>

      {/* 4 Sensor Calibration Struts */}
      <group ref={sensorStrutsRef}>
        {[0, 1, 2, 3].map((i) => {
          const angle = (i * Math.PI) / 2;
          return (
            <mesh
              key={i}
              position={[Math.cos(angle) * 1.78, Math.sin(angle) * 1.78, 0]}
              rotation={[0, 0, angle + Math.PI / 2]}
            >
              <boxGeometry args={[0.05, 0.42, 0.05]} />
              <meshStandardMaterial color="#2c3342" roughness={0.3} metalness={0.9} />
            </mesh>
          );
        })}
      </group>
    </group>
  );
}

// Scene Root with gentle cursor parallax
function SceneContainer({ isHovered }: { isHovered: boolean }) {
  const groupRef = useRef<THREE.Group>(null!);

  useFrame((state) => {
    if (!groupRef.current) return;
    // Subtly tilt towards cursor with smooth damping
    const targetY = (state.pointer.x * Math.PI) / 14;
    const targetX = (-state.pointer.y * Math.PI) / 16;

    groupRef.current.rotation.y = THREE.MathUtils.lerp(groupRef.current.rotation.y, targetY, 0.04);
    groupRef.current.rotation.x = THREE.MathUtils.lerp(groupRef.current.rotation.x, targetX, 0.04);
  });

  return (
    <group ref={groupRef} rotation={[0.12, -0.22, 0]}>
      {/* Key Light: Crisp chamfer highlights */}
      <directionalLight position={[4, 6, 4]} intensity={1.5} color="#ffffff" />
      {/* Rim Light: Cool slate rim */}
      <directionalLight position={[-5, -4, -3]} intensity={0.7} color="#60a5fa" />
      {/* Fill Light: Soft ambient base */}
      <ambientLight intensity={0.4} color="#0d1117" />
      {/* Aperture Focus Point Light */}
      <pointLight position={[0, 0, 0]} intensity={1.6} color="#00f2b2" distance={3.5} />

      <InstrumentRig isHovered={isHovered} />
      <SignalParticleStreams isHovered={isHovered} />
    </group>
  );
}

// Main Export Component
export default function HeroSignalRing() {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      className="relative w-full h-[380px] sm:h-[440px] lg:h-[500px] flex items-center justify-center select-none"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Subtle radial depth gradient behind instrument */}
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(0,242,178,0.07)_0%,rgba(8,9,11,0)_70%)] pointer-events-none" />

      <Suspense fallback={
        <div className="w-full h-full flex items-center justify-center text-xs font-mono-num text-white/40">
          INITIALIZING PRECISION INSTRUMENT...
        </div>
      }>
        <Canvas
          camera={{ position: [0, 0, 5.0], fov: 42 }}
          dpr={[1, 2]}
          gl={{ antialias: true, alpha: true }}
          style={{ pointerEvents: 'auto', background: 'transparent' }}
        >
          <SceneContainer isHovered={isHovered} />
        </Canvas>
      </Suspense>

      {/* Floating Precision Telemetry Badge */}
      <div className="absolute bottom-3 left-4 md:left-6 glass-panel px-3.5 py-1.5 rounded-lg text-xs font-mono-num flex items-center gap-3 border border-white/10 shadow-xl pointer-events-none">
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#00f2b2] animate-pulse" />
          <span className="text-[#00f2b2] font-semibold">PASS STREAM: 98.4%</span>
        </div>
        <span className="text-white/20">|</span>
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#ff4d4d]" />
          <span className="text-[#ff4d4d] font-semibold">REGRESSION: 1.6%</span>
        </div>
      </div>
    </div>
  );
}
