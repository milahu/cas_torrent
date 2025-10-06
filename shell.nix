{ pkgs ? import <nixpkgs> { } }:

let pp2 = {
  rtorrent-rpc = pkgs.python3.pkgs.callPackage ./nix/rtorrent-rpc {
    bencode2 = pkgs.python3.pkgs.callPackage ./nix/bencode2 { };
  };
  torrent-models = pkgs.python3.pkgs.callPackage ./nix/torrent-models {
    bencode-rs = pkgs.python3.pkgs.callPackage ./nix/bencode-rs { };
  };
  tree-sitter = pkgs.python3.pkgs.callPackage ./nix/tree-sitter { };
}; in

pkgs.mkShell {
  buildInputs = with pkgs; [
    (python3.withPackages (pp: (with pp; [
      qbittorrent-api
    ]) ++ (with pp2; [
      rtorrent-rpc
      # torrent-models
      tree-sitter
    ])))
    # pp2.tree-sitter
    tree-sitter
  ];
}
