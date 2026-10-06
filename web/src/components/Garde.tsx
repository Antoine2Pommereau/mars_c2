import { Component, type ReactNode } from "react";

/** Limite d'erreur : un composant qui échoue (la carte sans WebGL, par exemple) n'emporte pas le reste de l'écran. */
export default class Garde extends Component<{ message: string; children: ReactNode }, { erreur: boolean }> {
  state = { erreur: false };
  static getDerivedStateFromError() { return { erreur: true }; }
  render() {
    if (!this.state.erreur) return this.props.children;
    return <div className="absolute inset-0 flex items-center justify-center text-[12.5px] text-muted">{this.props.message}</div>;
  }
}
