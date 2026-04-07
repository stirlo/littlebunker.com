# _plugins/slug_index.rb
# Generates /slugs.json at build time for 404 fuzzy redirect matching.

require 'json'

Jekyll::Hooks.register :site, :post_write do |site|
  entries = []

  site.posts.docs.each do |doc|
    slug = doc.data['slug'] || doc.url.split('/').reject(&:empty?).last
    entries << { 'url' => doc.url, 'slug' => slug.to_s, 'title' => doc.data['title'].to_s }
  end

  site.collections.each do |label, collection|
    collection.docs.each do |doc|
      slug = doc.data['slug'] || doc.url.split('/').reject(&:empty?).last
      entries << { 'url' => doc.url, 'slug' => slug.to_s, 'title' => doc.data['title'].to_s }
    end
  end

  output_path = File.join(site.dest, 'slugs.json')
  File.write(output_path, JSON.generate(entries))
  Jekyll.logger.info "SlugIndex:", "Wrote #{entries.size} entries to /slugs.json"
end
