{
  # Linux dev environment for SMO Archipelago.
  #
  # Replaces the Windows setup wizard's toolchain-install step: everything
  # scripts/build_switchmod.py and scripts/extract_shine_map.py need is put
  # on PATH by `nix develop`. See docs/build-linux.md for the workflow.
  description = "SMO Archipelago (Spicy Meatball Overdrive) — Linux dev toolchain";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems
        (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs:
        let
          # LibHakkun's libc++ headers are ABI-pinned to LLVM 19 (see
          # _setup/prereqs.py LLVM_MAJOR). Unwrapped: the Nix cc-wrapper
          # injects host-glibc include/link flags that would contaminate the
          # freestanding --target=aarch64-none-elf build — Hakkun brings its
          # own musl + libc++ via lib/std/ (downloaded by
          # setup_libcxx_prepackaged.py at first build).
          llvm = pkgs.llvmPackages_19;

          # clang-unwrapped's builtin resource headers (arm_neon.h, stdarg.h,
          # …) live in the separate .lib output, so the bare binary cannot
          # find them relative to itself the way a normal LLVM install can.
          # These thin wrappers pin -resource-dir; everything else about the
          # compiler stays unwrapped (no host-glibc flag injection).
          clangResourceDir = "${pkgs.lib.getLib llvm.clang-unwrapped}/lib/clang/${pkgs.lib.versions.major llvm.clang-unwrapped.version}";
          clangCross = pkgs.runCommand "clang19-cross" { } ''
            mkdir -p $out/bin
            for tool in clang clang++; do
              cat > $out/bin/$tool <<EOF
            #!${pkgs.runtimeShell}
            exec ${llvm.clang-unwrapped}/bin/$tool -resource-dir ${clangResourceDir} "\$@"
            EOF
              chmod +x $out/bin/$tool
            done
          '';

          # Hakkun's cmake glue shells out to bare `python` / `python3`:
          # elf2nso.py needs lz4 + pyelftools, bake_hashes.py needs mmh3.
          # The same interpreter serves extract_shine_map.py's venv bootstrap
          # (Python 3.12 required — oead has no 3.13+ wheel).
          pythonEnv = pkgs.python312.withPackages (ps: with ps; [
            lz4
            pyelftools
            mmh3
            pytest         # apworld/smo_archipelago/tests/ (AP-independent subset)
            pytest-asyncio # switch_server / bridge tests are async
          ]);
        in
        {
          default = pkgs.mkShell {
            packages = [
              clangCross           # clang/clang++ for the aarch64-none-elf cross-compile
              llvm.lld             # ld.lld — toolchain.cmake passes -fuse-ld=lld
              llvm.llvm            # llvm-nm etc. for symbol discovery workflows
              pkgs.cmake
              pkgs.ninja
              pythonEnv
              pkgs.hactool         # NSP/XCI → RomFS for extract_shine_map.py
              pkgs.curl            # setup_libcxx_prepackaged.py downloads via curl
            ];
            # mkShell's stdenv additionally provides gcc/g++ (and exports
            # CC/CXX), which sail — LibHakkun's host-side symbol-DB binary —
            # is built with.

            shellHook = ''
              # extract_shine_map.py pip-installs the oead manylinux wheel
              # into scripts/.extract-venv; its _oead.so links libstdc++ and
              # zlib, which NixOS does not put on the default loader path.
              export LD_LIBRARY_PATH=${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ]}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
            '';
          };
        });
    };
}
