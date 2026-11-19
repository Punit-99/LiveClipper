/**
 * Mapping of known YouTube channel IDs to human-readable creator/channel names.
 */
export const KNOWN_CHANNELS = {
  // Configured & popular priority streamers
  'ucwsdfcihy2dbi3gb5uykgxa': 'IShowSpeed',
  'uc0iwrlai-bawci_e9mylngw': 'CarryisLive',
  'ucx6f-retgiz7xf_vkmmx2yq': 'Mythpat',
  'ucwxcauwjf3b_s6fflqoh7rw': 'GamerFleet',
  'ucfwkgzke-edtz83r6wzhmow': 'Live Insaan',
  'ucqfbn7ut5tizjahmo_rgfyw': 'Snax Gaming',
  'uc7cs8q-gjrlgwj4a8omcmxg': 'CarryMinati',
  'ucvq0c-sikklfaxyhyre1ysg': 'Mortal',
  'ucsiddxz6v4flv-s3nps49aq': 'Techno Gamerz',
  'ucg4eh77kg_x9f0w_de7g1rg': 'Dynamo Gaming',
  'ucjqf3hpupi_1c64lpd2w0_g': 'Total Gaming',
  'uc0pa8_yvgqx-r4c7d2p6fa': 'Scout',
  'ucxfj48u5g1t5k-p8g9f5j9a': 'Jonathan Gaming',
  'uclvgjrkf4k4kq2r4h8_wb9g': 'Mythpat',
};

/**
 * Returns a human-friendly channel display name instead of an ugly channel ID.
 * e.g. "ucwsdfcihy2dbi3gb5uykgxa" -> "IShowSpeed"
 *      "kaicenat" -> "kaicenat"
 *      "#kaicenat" -> "kaicenat"
 */
export function formatChannelName(channel) {
  if (!channel) return 'Unknown';
  const clean = String(channel).trim();
  const lower = clean.toLowerCase().replace(/^#/, '');

  if (KNOWN_CHANNELS[lower]) {
    return KNOWN_CHANNELS[lower];
  }

  // Strip leading '#' if present
  if (clean.startsWith('#')) {
    return clean.slice(1);
  }

  return clean;
}
